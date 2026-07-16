"""
SPL query safety validation - the most security-critical module in this
app, since every data source's search string is untrusted input supplied
by whoever holds the dsv_manage_datasources capability, but is *executed*
under the dedicated dsv_service identity (see security.py / HANDOFF.md).

Fail closed throughout: any parser failure, ambiguity, denylisted command,
or disallowed leading command results in rejection. Nothing here ever
executes a search itself or calls the network - it only classifies a
query string given a ParsedSearch produced by a SearchParser
implementation, so the whole safety policy is testable without Splunk.

Design follows HANDOFF.md's locked spec:
  1. Parse via the platform's /services/search/parser endpoint (handles
     pipes, comments, quoting, subsearches, macro expansion) instead of a
     naive regex. The real HTTP call lives in Phase 3; this module only
     depends on the SearchParser protocol below.
  2. Normalize command names; reject any denylisted command anywhere in
     the query (main pipeline or nested subsearch).
  3. Require a read-only leading command; anything else needs an
     explicit admin allowlist.
  4. Reject if the parser is unavailable or the parse is ambiguous.
  5. Append "| head 1" only for a pure streaming event search with no
     transforming/generating/limiting command anywhere in the main
     pipeline; otherwise run the query unmodified and check
     resultCount >= 1.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------

# Commands that write, alert, execute code, or otherwise have side effects
# or escalate privilege. Never permitted, anywhere in the query, for any
# caller - this is not admin-overridable. Extend as new risky commands are
# identified; names are matched case-insensitively after normalization.
DEFAULT_DENYLIST = frozenset({
    "collect",
    "outputlookup",
    "outputcsv",
    "mcollect",
    "meventcollect",
    "tscollect",
    "sendemail",
    "sendalert",
    "script",
    "run",
    "rest",
    "delete",
    "map",
    "loadjob",
    "savedsearch",
    "dbxquery",
})

# Leading (first pipeline stage) commands that are inherently read-only and
# require no special privilege to use as the start of a data source query.
DEFAULT_READONLY_LEADING_COMMANDS = frozenset({
    "search",
    "tstats",
    "mstats",
    "from",
    "datamodel",
    "metadata",
    "inputlookup",
})

# Commands that are safe to appear anywhere within a *pure streaming event
# search* - i.e. they operate per-event and never aggregate, generate, or
# truncate the result set. Only used to decide whether appending
# "| head 1" is safe; has no bearing on the denylist/leading-command checks.
STREAMING_COMMANDS = frozenset({
    "search",
    "where",
    "eval",
    "rex",
    "fields",
    "rename",
    "table",
    "lookup",
    "spath",
    "kv",
    "convert",
    "fillnull",
    "mvexpand",
    "strcat",
    "replace",
    "eval",
    "regex",
    "nomv",
    "makemv",
})

# Anything not in STREAMING_COMMANDS is treated as blocking the head-1
# optimization by default (fail closed on the *optimization*, not on
# safety - the unmodified query still runs and is still checked for
# resultCount >= 1).


@dataclass
class ParsedSearch:
    """
    The result of parsing an SPL string. Real (Phase 3) implementations
    build this from the JSON returned by /services/search/parser after
    macro expansion; tests build it directly.
    """
    leading_command: str
    main_pipeline_commands: List[str]
    all_commands: List[str] = field(default_factory=list)
    ambiguous: bool = False
    ambiguous_reason: Optional[str] = None

    def __post_init__(self):
        self.leading_command = _normalize(self.leading_command)
        self.main_pipeline_commands = [_normalize(c) for c in self.main_pipeline_commands]
        if not self.all_commands:
            self.all_commands = list(self.main_pipeline_commands)
        else:
            self.all_commands = [_normalize(c) for c in self.all_commands]


class ParserUnavailableError(Exception):
    """Raised by a SearchParser implementation when the parser endpoint
    could not be reached or returned an unusable response."""


class SearchParser:
    """
    Protocol for anything that can turn an SPL string into a ParsedSearch.
    Not a real ABC on purpose - any object with a matching parse() method
    (including a test double / lambda-backed fake) satisfies this.
    """

    def parse(self, spl_query: str) -> ParsedSearch:
        raise NotImplementedError


@dataclass
class QueryValidationResult:
    allowed: bool
    reason: str
    normalized_query: str
    executable_query: str
    leading_command: Optional[str] = None
    all_commands: List[str] = field(default_factory=list)
    head_appended: bool = False


def _normalize(command_name):
    return (command_name or "").strip().lower().lstrip("|").strip()


def compute_query_hash(spl_query: str) -> str:
    """
    Stable content hash used to detect that a query changed (bumps
    query_version and resets status to NOT RUN per HANDOFF's status
    rules) - hashed on the exact stored string, whitespace included, so
    any edit at all is detected.
    """
    return hashlib.sha256(spl_query.encode("utf-8")).hexdigest()


class QueryValidator:
    def __init__(
        self,
        parser: SearchParser,
        denylist: Iterable[str] = DEFAULT_DENYLIST,
        readonly_leading_commands: Iterable[str] = DEFAULT_READONLY_LEADING_COMMANDS,
        admin_leading_commands: Iterable[str] = frozenset(),
        streaming_commands: Iterable[str] = STREAMING_COMMANDS,
    ):
        self._parser = parser
        self._denylist = frozenset(_normalize(c) for c in denylist)
        self._readonly_leading = frozenset(_normalize(c) for c in readonly_leading_commands)
        self._admin_leading = frozenset(_normalize(c) for c in admin_leading_commands)
        self._streaming = frozenset(_normalize(c) for c in streaming_commands)

    def validate(self, spl_query: str, is_admin: bool = False) -> QueryValidationResult:
        """
        Never raises for expected failure modes (unparseable, ambiguous,
        denylisted, disallowed leading command) - always returns a
        QueryValidationResult with allowed=False and a human-readable
        reason in those cases, so callers (REST handlers, the worker)
        can surface it directly without a try/except around business logic.
        """
        query = (spl_query or "").strip()
        if not query:
            return QueryValidationResult(
                allowed=False, reason="query is empty",
                normalized_query=query, executable_query=query,
            )

        try:
            parsed = self._parser.parse(query)
        except ParserUnavailableError as e:
            return QueryValidationResult(
                allowed=False,
                reason=f"search parser unavailable: {e}",
                normalized_query=query, executable_query=query,
            )
        except Exception as e:  # fail closed on any unexpected parser error
            return QueryValidationResult(
                allowed=False,
                reason=f"search parser raised an unexpected error: {e}",
                normalized_query=query, executable_query=query,
            )

        if parsed is None:
            return QueryValidationResult(
                allowed=False, reason="search parser returned no result",
                normalized_query=query, executable_query=query,
            )

        if parsed.ambiguous:
            return QueryValidationResult(
                allowed=False,
                reason=f"parse is ambiguous: {parsed.ambiguous_reason or 'unspecified'}",
                normalized_query=query, executable_query=query,
            )

        if not parsed.leading_command:
            return QueryValidationResult(
                allowed=False, reason="could not determine leading command",
                normalized_query=query, executable_query=query,
            )

        denylisted_hit = self._denylist.intersection(parsed.all_commands)
        if denylisted_hit:
            return QueryValidationResult(
                allowed=False,
                reason=f"query uses disallowed command(s): {sorted(denylisted_hit)}",
                normalized_query=query, executable_query=query,
                leading_command=parsed.leading_command,
                all_commands=parsed.all_commands,
            )

        if parsed.leading_command not in self._readonly_leading:
            if not (is_admin and parsed.leading_command in self._admin_leading):
                return QueryValidationResult(
                    allowed=False,
                    reason=(
                        f"leading command '{parsed.leading_command}' is not "
                        f"read-only and is not on the admin allowlist"
                    ),
                    normalized_query=query, executable_query=query,
                    leading_command=parsed.leading_command,
                    all_commands=parsed.all_commands,
                )

        head_appended = False
        executable_query = query
        if self._is_pure_streaming(parsed):
            executable_query = f"{query} | head 1"
            head_appended = True

        return QueryValidationResult(
            allowed=True,
            reason="ok",
            normalized_query=query,
            executable_query=executable_query,
            leading_command=parsed.leading_command,
            all_commands=parsed.all_commands,
            head_appended=head_appended,
        )

    def _is_pure_streaming(self, parsed: ParsedSearch) -> bool:
        if parsed.leading_command != "search":
            return False
        return all(cmd in self._streaming for cmd in parsed.main_pipeline_commands)
