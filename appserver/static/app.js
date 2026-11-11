/*
 * Data Source Validator - app.js
 *
 * Drives the four custom Simple XML views (setup.xml, home.xml,
 * manage.xml, health.xml) against this app's own REST endpoints (restmap.conf /
 * web.conf). Uses splunkjs/mvc's service object so requests carry the
 * viewer's own Splunk Web session automatically - no hand-rolled auth.
 *
 * NOTE: the exact response envelope a `scripttype = persist` REST
 * endpoint returns through Splunk Web's proxy could not be verified
 * against a live Splunk instance while this was written (see
 * HANDOFF.md's "Verify in your environment" notes for the same caveat
 * elsewhere in this app) - parseResponse() below is deliberately
 * defensive about that. Confirm against a real install and simplify
 * once confirmed.
 */
require([
    'jquery',
    'splunkjs/mvc',
    'splunkjs/mvc/simplesplunkview'
], function ($, mvc) {
    'use strict';

    // Live report: with mvc.createService() called with no arguments, a POST
    // was observed going to /servicesNS/<the just-typed service-account
    // username>/datasource_validator/..., not the logged-in admin's own
    // session (confirmed - the tester was authenticated as sc_admin, not as
    // that account) and not `nobody`. All of this app's own KV Store /
    // storage/passwords config is app-level, not user-private, and the
    // server side (bin/app/splunk_client.py's DEFAULT_OWNER) already
    // targets "nobody" for it - pin the client to the same explicit
    // namespace instead of relying on whatever mvc.createService() resolves
    // implicitly, which produced the wrong owner at least once.
    var service = mvc.createService({ owner: 'nobody' });
    var POLL_INTERVAL_MS = 4000;
    var HEALTH_POLL_INTERVAL_MS = 5000;

    // SPECULATIVE FIX (unconfirmed against a live instance): Splunk Web
    // CSRF-protects state-changing methods (POST/PUT/DELETE) but not GET,
    // requiring an X-Splunk-Form-Key header sourced from a
    // splunkweb_csrf_token_<port> cookie. A live report showed exactly
    // this signature - GET succeeded, POST failed with a bare "Forbidden"
    // before reaching this app's own REST handler. splunkjs's Service
    // object exposes no way to attach a custom header through post()/
    // del() (only the lower-level request() takes one), so this attaches
    // the header to every jQuery AJAX call on the page instead - additive
    // only, never removes any protection. If this wasn't the real cause,
    // it's a no-op: the header is simply ignored when not required.
    $.ajaxPrefilter(function (options, originalOptions, jqXHR) {
        var match = document.cookie.match(/splunkweb_csrf_token_\d+=([^;]+)/);
        if (match) {
            jqXHR.setRequestHeader('X-Splunk-Form-Key', decodeURIComponent(match[1]));
        }
    });

    function parseResponse(response) {
        var data = response && response.data;
        if (data && typeof data === 'object' && typeof data.payload === 'string') {
            try {
                return JSON.parse(data.payload);
            } catch (e) {
                return data.payload;
            }
        }
        // Some paths through Splunk Web's proxy hand back the persist
        // handler's payload already parsed into an object rather than as
        // the JSON string it's normally wrapped in - use it directly
        // instead of returning the outer {payload: ..., status: ...} shell.
        if (data && typeof data === 'object' && data.payload && typeof data.payload === 'object') {
            return data.payload;
        }
        if (typeof data === 'string') {
            try {
                return JSON.parse(data);
            } catch (e) {
                return data;
            }
        }
        return data;
    }

    // Confirmed live: a failed request's `err` from splunkjs looks like
    // {status: 400, error: "Bad Request", data: '{"error": "<reason>"}'} -
    // the handler's own JSON body arrives as a *string* under `data`, and
    // the top-level `error` is only the HTTP status text. So look inside
    // data/payload first and fall back to the status text last.
    function extractErrorMessage(obj, depth) {
        if (typeof obj === 'string') {
            try { obj = JSON.parse(obj); } catch (e) { return null; }
        }
        if (!obj || typeof obj !== 'object' || depth > 4) { return null; }

        var nested = extractErrorMessage(obj.data, depth + 1) || extractErrorMessage(obj.payload, depth + 1);
        if (nested) { return nested; }

        // Standard splunkd REST error envelope, for errors raised by the
        // platform before this app's own handler runs (e.g. a 403).
        if (Array.isArray(obj.messages) && obj.messages.length) {
            var texts = obj.messages
                .map(function (m) { return m && (m.text || m.message); })
                .filter(Boolean);
            if (texts.length) { return texts.join('; '); }
        }
        if (typeof obj.error === 'string' && obj.error) { return obj.error; }
        return null;
    }

    function restGet(path, params) {
        var deferred = $.Deferred();
        service.get(path, params || {}, function (err, response) {
            if (err) {
                deferred.reject(err);
                return;
            }
            deferred.resolve(parseResponse(response));
        });
        return deferred.promise();
    }

    function restPost(path, data) {
        var deferred = $.Deferred();
        service.post(path, data || {}, function (err, response) {
            if (err) {
                deferred.reject(err);
                return;
            }
            deferred.resolve(parseResponse(response));
        });
        return deferred.promise();
    }

    function restDelete(path) {
        var deferred = $.Deferred();
        service.del(path, {}, function (err, response) {
            if (err) {
                deferred.reject(err);
                return;
            }
            deferred.resolve(parseResponse(response));
        });
        return deferred.promise();
    }

    function errorText(err) {
        if (!err) { return 'unknown error'; }

        var extracted = extractErrorMessage(err, 0);
        if (extracted) { return extracted; }

        if (err.message) { return err.message; }

        var status = err.status || (err.response && err.response.statusCode);
        var suffix = status ? ' (HTTP ' + status + ')' : '';

        try {
            return 'unrecognized error response' + suffix + ': ' + JSON.stringify(err);
        } catch (e) {
            return 'unrecognized error response' + suffix;
        }
    }

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        attrs = attrs || {};
        Object.keys(attrs).forEach(function (key) {
            if (key === 'class') {
                node.className = attrs[key];
            } else if (key === 'text') {
                node.textContent = attrs[key];
            } else {
                node.setAttribute(key, attrs[key]);
            }
        });
        (children || []).forEach(function (child) {
            if (child) { node.appendChild(child); }
        });
        return node;
    }

    function statusBadge(status, label) {
        return el('span', { class: 'dsv-badge dsv-badge-' + String(status).toLowerCase(), text: label || status });
    }

    // ------------------------------------------------------------------
    // Setup view
    // ------------------------------------------------------------------

    function initSetup(root) {
        restGet('datasource_validator/config').done(function (config) {
            renderSetupForm(root, config || {});
        }).fail(function (err) {
            console.error('[dsv]', err);
            root.innerHTML = '';
            root.appendChild(el('p', { class: 'dsv-error', text: 'Could not load setup status: ' + errorText(err) }));
            renderSetupForm(root, {});
        });
    }

    function renderSetupForm(root, config) {
        root.innerHTML = '';

        var status = el('p', {
            class: config.configured ? 'dsv-ok' : 'dsv-warning',
            text: config.configured
                ? 'A service account is configured (' + config.service_username + ').'
                : 'No service account is configured yet - validations cannot run until you save one below.'
        });

        var usernameInput = el('input', { type: 'text', id: 'dsv-username', placeholder: 'svc-dsv@example.com' });
        var passwordInput = el('input', { type: 'password', id: 'dsv-password', placeholder: 'password' });
        var thresholdInput = el('input', { type: 'number', id: 'dsv-threshold', placeholder: '3600', min: '0' });
        var timeoutInput = el('input', { type: 'number', id: 'dsv-timeout', placeholder: '120', min: '1' });

        var settings = (config.settings || {});
        thresholdInput.value = settings.default_stale_threshold_sec || '';
        timeoutInput.value = settings.default_dispatch_timeout_sec || '';

        var saveButton = el('button', { type: 'button', class: 'dsv-button', text: 'Save' });
        var message = el('div', { class: 'dsv-form-message' });

        saveButton.addEventListener('click', function () {
            var username = usernameInput.value.trim();
            var password = passwordInput.value;
            if (!username || !password) {
                message.textContent = 'Username and password are both required.';
                message.className = 'dsv-form-message dsv-error';
                return;
            }
            saveButton.disabled = true;
            message.textContent = 'Saving…';
            message.className = 'dsv-form-message';

            restPost('datasource_validator/config', {
                username: username,
                password: password,
                default_stale_threshold_sec: thresholdInput.value,
                default_dispatch_timeout_sec: timeoutInput.value
            }).done(function (result) {
                if (result && result.warning) {
                    message.textContent = 'Saved with a warning: ' + result.warning;
                    message.className = 'dsv-form-message dsv-warning';
                } else {
                    message.textContent = 'Saved. The validation worker is now enabled.';
                    message.className = 'dsv-form-message dsv-ok';
                }
                passwordInput.value = '';
            }).fail(function (err) {
                console.error('[dsv]', err);
                message.textContent = 'Save failed: ' + errorText(err);
                message.className = 'dsv-form-message dsv-error';
            }).always(function () {
                saveButton.disabled = false;
            });
        });

        var form = el('div', { class: 'dsv-form' }, [
            status,
            el('label', { text: 'Service account username' }),
            usernameInput,
            el('label', { text: 'Service account password' }),
            passwordInput,
            el('label', { text: 'Default staleness threshold (seconds)' }),
            thresholdInput,
            el('label', { text: 'Default dispatch timeout (seconds)' }),
            timeoutInput,
            saveButton,
            message
        ]);
        root.appendChild(form);
    }

    // ------------------------------------------------------------------
    // Home view - platform / data source status table
    // ------------------------------------------------------------------

    function initHome(root) {
        loadHome(root);
    }

    function loadHome(root) {
        // restGet()'s deferred resolves with a single value (the already-
        // parsed body), not jQuery.ajax()'s native (data, textStatus,
        // jqXHR) triple - so with two restGet() calls, $.when's combined
        // done callback hands each one's resolved value straight through
        // as platformsResp/datasourcesResp, not wrapped in an array.
        // Confirmed live: indexing platformsResp[0] (a jQuery.ajax idiom)
        // always read undefined here, silently rendering an empty table
        // even with real platforms/data sources present.
        $.when(
            restGet('datasource_validator/platforms'),
            restGet('datasource_validator/datasources')
        ).done(function (platformsResp, datasourcesResp) {
            renderHome(root, (platformsResp || {}).platforms || [], (datasourcesResp || {}).datasources || []);
        }).fail(function (err) {
            console.error('[dsv]', err);
            root.innerHTML = '';
            root.appendChild(el('p', { class: 'dsv-error', text: 'Could not load status: ' + errorText(err) }));
        });
    }

    function renderHome(root, platforms, datasources) {
        root.innerHTML = '';

        if (!platforms.length) {
            var empty = el('p', { class: 'dsv-empty' });
            empty.appendChild(document.createTextNode('No platforms are configured yet. Add them on the '));
            empty.appendChild(el('a', { href: 'manage', text: 'Manage' }));
            empty.appendChild(document.createTextNode(' page.'));
            root.appendChild(empty);
            return;
        }

        var byPlatform = {};
        datasources.forEach(function (ds) {
            byPlatform[ds.platform_id] = byPlatform[ds.platform_id] || [];
            byPlatform[ds.platform_id].push(ds);
        });

        var runAllButton = el('button', { type: 'button', class: 'dsv-button', text: 'Run All Enabled' });
        var runStatus = el('div', { class: 'dsv-run-status' });
        runAllButton.addEventListener('click', function () {
            startRun(root, runStatus, {});
        });

        root.appendChild(el('div', { class: 'dsv-toolbar' }, [runAllButton, runStatus]));

        var table = el('table', { class: 'dsv-table' });
        var thead = el('thead', {}, [
            el('tr', {}, [
                el('th', { text: 'Platform / Data Source' }),
                el('th', { text: 'Status' }),
                el('th', { text: 'Last Result' }),
                el('th', { text: '' })
            ])
        ]);
        table.appendChild(thead);

        var tbody = el('tbody');
        platforms.forEach(function (platform) {
            var runPlatformButton = el('button', { type: 'button', class: 'dsv-button-small', text: 'Run' });
            runPlatformButton.addEventListener('click', function () {
                var ids = (byPlatform[platform.platform_id] || []).map(function (ds) { return ds.datasource_id; });
                startRun(root, runStatus, { datasource_ids: JSON.stringify(ids) });
            });

            tbody.appendChild(el('tr', { class: 'dsv-platform-row' }, [
                el('td', { text: platform.name + ' (' + platform.datasource_count + ')' }),
                el('td', {}, [statusBadge(platform.status, platform.status_label)]),
                el('td', { text: '' }),
                el('td', {}, [runPlatformButton])
            ]));

            (byPlatform[platform.platform_id] || []).forEach(function (ds) {
                tbody.appendChild(el('tr', { class: 'dsv-datasource-row' }, [
                    el('td', { text: ' ' + ds.name }),
                    el('td', {}, [statusBadge(ds.status, ds.status_label)]),
                    el('td', { text: ds.last_result_time || 'never' }),
                    el('td')
                ]));
            });
        });
        table.appendChild(tbody);
        root.appendChild(table);
    }

    function startRun(root, statusEl, extraFields) {
        statusEl.textContent = 'Starting run…';
        var payload = { trigger_type: 'manual' };
        Object.keys(extraFields || {}).forEach(function (k) { payload[k] = extraFields[k]; });

        restPost('datasource_validator/run', payload).done(function (run) {
            pollRun(root, statusEl, run.run_id);
        }).fail(function (err) {
            console.error('[dsv]', err);
            statusEl.textContent = 'Could not start run: ' + errorText(err);
        });
    }

    function pollRun(root, statusEl, runId) {
        var cancelButton = el('button', { type: 'button', class: 'dsv-button-small', text: 'Cancel run' });
        cancelButton.addEventListener('click', function () {
            restPost('datasource_validator/run/' + runId, {});
        });

        function tick() {
            restGet('datasource_validator/run/' + runId).done(function (body) {
                var run = body.run || {};
                statusEl.innerHTML = '';
                statusEl.appendChild(document.createTextNode(
                    'Run ' + run.status + ': ' + run.completed_items + ' / ' + run.total_items + ' '
                ));
                if (run.status === 'RUNNING') {
                    statusEl.appendChild(cancelButton);
                    setTimeout(tick, POLL_INTERVAL_MS);
                } else {
                    loadHome(root);
                }
            }).fail(function (err) {
                console.error('[dsv]', err);
                statusEl.textContent = 'Lost track of run status: ' + errorText(err);
            });
        }
        tick();
    }

    // ------------------------------------------------------------------
    // Manage view - add / edit / delete platforms and data sources
    // ------------------------------------------------------------------

    var IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

    function initManage(root) {
        loadManage(root, null);
    }

    function loadManage(root, notice) {
        $.when(
            restGet('datasource_validator/platforms'),
            restGet('datasource_validator/datasources')
        ).done(function (platformsResp, datasourcesResp) {
            renderManage(
                root, (platformsResp || {}).platforms || [],
                (datasourcesResp || {}).datasources || [], notice
            );
        }).fail(function (err) {
            console.error('[dsv]', err);
            root.innerHTML = '';
            root.appendChild(el('p', { class: 'dsv-error', text: 'Could not load platforms: ' + errorText(err) }));
        });
    }

    function slugify(text) {
        return String(text).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64);
    }

    function formField(labelText, control, hint) {
        return el('div', { class: 'dsv-field' }, [
            el('label', { for: control.id, text: labelText }),
            control,
            hint ? el('p', { class: 'dsv-hint', text: hint }) : null
        ]);
    }

    function checkboxField(labelText, checkbox) {
        var label = el('label', { class: 'dsv-checkbox', for: checkbox.id }, [checkbox]);
        label.appendChild(document.createTextNode(' ' + labelText));
        return el('div', { class: 'dsv-field' }, [label]);
    }

    // On create the id follows the name until the user edits it by hand;
    // an existing record's id is its storage key and cannot change.
    function linkIdToName(nameInput, idInput, editing) {
        if (editing) {
            idInput.disabled = true;
            return;
        }
        var edited = false;
        idInput.addEventListener('input', function () { edited = true; });
        nameInput.addEventListener('input', function () {
            if (!edited) { idInput.value = slugify(nameInput.value); }
        });
    }

    function wholeNumber(text, fieldLabel, minimum, blankValue) {
        var trimmed = String(text).trim();
        if (trimmed === '') { return { value: blankValue }; }
        if (!/^\d+$/.test(trimmed) || Number(trimmed) < minimum) {
            return { error: fieldLabel + ' must be a whole number of at least ' + minimum + '.' };
        }
        return { value: trimmed };
    }

    // Builds the shared form frame. collect() returns {error} or
    // {path, payload, notice}; a successful save reloads the page.
    function buildForm(ctx, title, fieldNodes, collect) {
        var saveButton = el('button', { type: 'submit', class: 'dsv-button', text: 'Save' });
        var cancelButton = el('button', { type: 'button', class: 'dsv-button dsv-button-secondary', text: 'Cancel' });
        var message = el('div', { class: 'dsv-form-message', role: 'status' });
        var form = el('form', { class: 'dsv-form dsv-form-wide dsv-panel', novalidate: 'novalidate' },
            [el('h3', { text: title })].concat(fieldNodes, [
                el('div', { class: 'dsv-form-actions' }, [saveButton, cancelButton]),
                message
            ]));

        cancelButton.addEventListener('click', ctx.close);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var request = collect();
            if (request.error) {
                message.textContent = request.error;
                message.className = 'dsv-form-message dsv-error';
                return;
            }
            saveButton.disabled = true;
            message.textContent = 'Saving…';
            message.className = 'dsv-form-message';
            restPost(request.path, request.payload).done(function () {
                ctx.reload(request.notice);
            }).fail(function (err) {
                console.error('[dsv]', err);
                message.textContent = 'Save failed: ' + errorText(err);
                message.className = 'dsv-form-message dsv-error';
                saveButton.disabled = false;
            });
        });
        return form;
    }

    function buildPlatformForm(ctx, existing) {
        var editing = !!existing;
        var nameInput = el('input', { type: 'text', id: 'dsv-platform-name', maxlength: '200' });
        var idInput = el('input', { type: 'text', id: 'dsv-platform-id', maxlength: '64' });
        var descriptionInput = el('input', { type: 'text', id: 'dsv-platform-description', maxlength: '1000' });
        var sortInput = el('input', { type: 'number', id: 'dsv-platform-sort', min: '0', step: '1' });
        var enabledInput = el('input', { type: 'checkbox', id: 'dsv-platform-enabled' });

        nameInput.value = editing ? existing.name : '';
        idInput.value = editing ? existing.platform_id : '';
        descriptionInput.value = editing ? (existing.description || '') : '';
        sortInput.value = editing ? existing.sort_order : 0;
        enabledInput.checked = editing ? !!existing.enabled : true;
        linkIdToName(nameInput, idInput, editing);

        return buildForm(ctx, editing ? 'Edit platform' : 'Add platform', [
            formField('Name', nameInput),
            formField('ID', idInput, editing
                ? 'The ID cannot be changed after the platform is created.'
                : 'Letters, numbers, underscores and hyphens. Cannot be changed later.'),
            formField('Description', descriptionInput),
            formField('Sort order', sortInput, 'Lower numbers are listed first.'),
            checkboxField('Enabled', enabledInput)
        ], function () {
            var name = nameInput.value.trim();
            var id = idInput.value.trim();
            var sort = wholeNumber(sortInput.value, 'Sort order', 0, '0');
            if (!name) { return { error: 'Name is required.' }; }
            if (sort.error) { return sort; }
            if (!editing) {
                if (!IDENTIFIER_RE.test(id)) {
                    return { error: 'ID is required and may only contain letters, numbers, underscores and hyphens.' };
                }
                if (ctx.platforms.some(function (p) { return p.platform_id === id; })) {
                    return { error: 'A platform with the ID "' + id + '" already exists.' };
                }
            }
            var payload = {
                name: name,
                description: descriptionInput.value.trim(),
                sort_order: sort.value,
                enabled: enabledInput.checked ? 'true' : 'false'
            };
            if (!editing) { payload.platform_id = id; }
            return {
                path: 'datasource_validator/platforms' + (editing ? '/' + encodeURIComponent(existing.platform_id) : ''),
                payload: payload,
                notice: 'Platform "' + name + '" ' + (editing ? 'saved.' : 'added.')
            };
        });
    }

    function buildDatasourceForm(ctx, existing, presetPlatformId) {
        var editing = !!existing;
        var platformSelect = el('select', { id: 'dsv-datasource-platform' });
        ctx.platforms.forEach(function (p) {
            platformSelect.appendChild(el('option', { value: p.platform_id, text: p.name }));
        });
        var nameInput = el('input', { type: 'text', id: 'dsv-datasource-name', maxlength: '200' });
        var idInput = el('input', { type: 'text', id: 'dsv-datasource-id', maxlength: '64' });
        var queryInput = el('textarea', { id: 'dsv-datasource-query', rows: '3', spellcheck: 'false' });
        var descriptionInput = el('input', { type: 'text', id: 'dsv-datasource-description', maxlength: '1000' });
        var staleInput = el('input', { type: 'number', id: 'dsv-datasource-stale', min: '1', step: '1' });
        var sortInput = el('input', { type: 'number', id: 'dsv-datasource-sort', min: '0', step: '1' });
        var enabledInput = el('input', { type: 'checkbox', id: 'dsv-datasource-enabled' });

        platformSelect.value = editing ? existing.platform_id : (presetPlatformId || platformSelect.value);
        platformSelect.disabled = editing;
        nameInput.value = editing ? existing.name : '';
        idInput.value = editing ? existing.datasource_id : '';
        queryInput.value = editing ? existing.spl_query : '';
        descriptionInput.value = editing ? (existing.description || '') : '';
        staleInput.value = editing && existing.stale_threshold_sec != null ? existing.stale_threshold_sec : '';
        sortInput.value = editing ? existing.sort_order : 0;
        enabledInput.checked = editing ? !!existing.enabled : true;
        linkIdToName(nameInput, idInput, editing);

        return buildForm(ctx, editing ? 'Edit data source' : 'Add data source', [
            formField('Platform', platformSelect, editing ? 'A data source cannot be moved to another platform.' : null),
            formField('Name', nameInput),
            formField('ID', idInput, editing
                ? 'The ID cannot be changed after the data source is created.'
                : 'Letters, numbers, underscores and hyphens. Cannot be changed later.'),
            formField('Search', queryInput,
                'A search that returns at least one event while the data source is healthy, for example: ' +
                'search index=main sourcetype=my:sourcetype | head 1. It runs as the validation service ' +
                'account, so it only sees indexes that account is allowed to search.' +
                (editing ? ' Changing the search resets the status to Not Run.' : '')),
            formField('Description', descriptionInput),
            formField('Staleness threshold (seconds)', staleInput, 'Leave blank to use the app default.'),
            formField('Sort order', sortInput, 'Lower numbers are listed first within the platform.'),
            checkboxField('Enabled', enabledInput)
        ], function () {
            var name = nameInput.value.trim();
            var id = idInput.value.trim();
            var query = queryInput.value.trim();
            var sort = wholeNumber(sortInput.value, 'Sort order', 0, '0');
            var stale = wholeNumber(staleInput.value, 'Staleness threshold', 1, '');
            if (!platformSelect.value) { return { error: 'Choose a platform.' }; }
            if (!name) { return { error: 'Name is required.' }; }
            if (!editing) {
                if (!IDENTIFIER_RE.test(id)) {
                    return { error: 'ID is required and may only contain letters, numbers, underscores and hyphens.' };
                }
                if (ctx.datasources.some(function (d) { return d.datasource_id === id; })) {
                    return { error: 'A data source with the ID "' + id + '" already exists.' };
                }
            }
            if (!query) { return { error: 'Search is required.' }; }
            if (stale.error) { return stale; }
            if (sort.error) { return sort; }
            var payload = {
                name: name,
                spl_query: query,
                description: descriptionInput.value.trim(),
                stale_threshold_sec: stale.value,
                sort_order: sort.value,
                enabled: enabledInput.checked ? 'true' : 'false'
            };
            if (!editing) {
                payload.datasource_id = id;
                payload.platform_id = platformSelect.value;
            }
            return {
                path: 'datasource_validator/datasources' + (editing ? '/' + encodeURIComponent(existing.datasource_id) : ''),
                payload: payload,
                notice: 'Data source "' + name + '" ' + (editing ? 'saved.' : 'added.')
            };
        });
    }

    function renderManage(root, platforms, datasources, notice) {
        root.innerHTML = '';

        var byPlatform = {};
        datasources.forEach(function (ds) {
            byPlatform[ds.platform_id] = byPlatform[ds.platform_id] || [];
            byPlatform[ds.platform_id].push(ds);
        });

        var noticeEl = el('div', { class: 'dsv-form-message dsv-ok', role: 'status', text: notice || '' });
        var formHost = el('div');
        var ctx = {
            platforms: platforms,
            datasources: datasources,
            close: function () { formHost.innerHTML = ''; },
            reload: function (message) { loadManage(root, message); }
        };

        function openForm(form) {
            noticeEl.textContent = '';
            formHost.innerHTML = '';
            formHost.appendChild(form);
            form.scrollIntoView({ block: 'nearest' });
            var first = form.querySelector('input:not([disabled]), select:not([disabled]), textarea');
            if (first) { first.focus(); }
        }

        function actionButton(text, onClick, extraClass) {
            var button = el('button', {
                type: 'button', class: 'dsv-button-small' + (extraClass ? ' ' + extraClass : ''), text: text
            });
            button.addEventListener('click', onClick);
            return button;
        }

        function remove(path, question, doneNotice) {
            if (!window.confirm(question)) { return; }
            restDelete(path).done(function () {
                ctx.reload(doneNotice);
            }).fail(function (err) {
                console.error('[dsv]', err);
                noticeEl.textContent = 'Delete failed: ' + errorText(err);
                noticeEl.className = 'dsv-form-message dsv-error';
            });
        }

        var addPlatformButton = el('button', { type: 'button', class: 'dsv-button', text: 'Add platform' });
        addPlatformButton.addEventListener('click', function () { openForm(buildPlatformForm(ctx, null)); });
        root.appendChild(el('div', { class: 'dsv-toolbar' }, [addPlatformButton]));
        root.appendChild(noticeEl);
        root.appendChild(formHost);

        if (!platforms.length) {
            root.appendChild(el('p', {
                class: 'dsv-empty',
                text: 'No platforms yet. Add a platform, then add the data sources to validate under it.'
            }));
            return;
        }

        var table = el('table', { class: 'dsv-table' }, [
            el('thead', {}, [
                el('tr', {}, [
                    el('th', { text: 'Platform / Data Source' }),
                    el('th', { text: 'ID' }),
                    el('th', { text: 'Description / Search' }),
                    el('th', { text: 'Enabled' }),
                    el('th', { text: '' })
                ])
            ])
        ]);
        var tbody = el('tbody');

        platforms.forEach(function (platform) {
            var children = byPlatform[platform.platform_id] || [];
            tbody.appendChild(el('tr', { class: 'dsv-platform-row' }, [
                el('td', { text: platform.name }),
                el('td', {}, [el('code', { class: 'dsv-code', text: platform.platform_id })]),
                el('td', { text: platform.description || '' }),
                el('td', { text: platform.enabled ? 'Yes' : 'No' }),
                el('td', { class: 'dsv-actions' }, [
                    actionButton('Add data source', function () {
                        openForm(buildDatasourceForm(ctx, null, platform.platform_id));
                    }),
                    actionButton('Edit', function () { openForm(buildPlatformForm(ctx, platform)); }),
                    actionButton('Delete', function () {
                        remove(
                            'datasource_validator/platforms/' + encodeURIComponent(platform.platform_id),
                            'Delete the platform "' + platform.name + '"' +
                                (children.length ? ' and its ' + children.length + ' data source(s)' : '') +
                                '? This cannot be undone.',
                            'Platform "' + platform.name + '" deleted.'
                        );
                    }, 'dsv-button-danger')
                ])
            ]));

            children.forEach(function (ds) {
                tbody.appendChild(el('tr', { class: 'dsv-datasource-row' }, [
                    el('td', { class: 'dsv-indent', text: ds.name }),
                    el('td', {}, [el('code', { class: 'dsv-code', text: ds.datasource_id })]),
                    el('td', {}, [el('code', { class: 'dsv-code dsv-query', text: ds.spl_query, title: ds.spl_query })]),
                    el('td', { text: ds.enabled ? 'Yes' : 'No' }),
                    el('td', { class: 'dsv-actions' }, [
                        actionButton('Edit', function () { openForm(buildDatasourceForm(ctx, ds)); }),
                        actionButton('Delete', function () {
                            remove(
                                'datasource_validator/datasources/' + encodeURIComponent(ds.datasource_id),
                                'Delete the data source "' + ds.name + '"? This cannot be undone.',
                                'Data source "' + ds.name + '" deleted.'
                            );
                        }, 'dsv-button-danger')
                    ])
                ]));
            });
        });
        table.appendChild(tbody);
        root.appendChild(table);
    }

    // ------------------------------------------------------------------
    // Health view
    // ------------------------------------------------------------------

    function initHealth(root) {
        loadHealth(root);
    }

    function loadHealth(root) {
        restGet('datasource_validator/health').done(function (health) {
            renderHealth(root, health || {});
            setTimeout(function () { loadHealth(root); }, HEALTH_POLL_INTERVAL_MS);
        }).fail(function (err) {
            console.error('[dsv]', err);
            root.innerHTML = '';
            root.appendChild(el('p', { class: 'dsv-error', text: 'Could not load health status: ' + errorText(err) }));
            setTimeout(function () { loadHealth(root); }, HEALTH_POLL_INTERVAL_MS);
        });
    }

    function renderHealth(root, health) {
        root.innerHTML = '';
        var alive = health.worker_alive;
        root.appendChild(el('p', {
            class: alive ? 'dsv-ok' : 'dsv-error',
            text: alive ? 'Validation worker is active.' : 'No active validation worker - a lease has not been renewed recently.'
        }));

        var lease = health.worker_lease;
        var list = el('dl', { class: 'dsv-health-list' });
        if (lease) {
            [
                ['Owner (search-head-cluster member)', lease.owner],
                ['Acquired', lease.acquired_time],
                ['Last heartbeat', lease.heartbeat_time],
                ['Expires', lease.expires_time]
            ].forEach(function (pair) {
                list.appendChild(el('dt', { text: pair[0] }));
                list.appendChild(el('dd', { text: pair[1] || '' }));
            });
        } else {
            list.appendChild(el('dt', { text: 'Lease' }));
            list.appendChild(el('dd', { text: 'No lease has been acquired yet.' }));
        }
        list.appendChild(el('dt', { text: 'Currently running' }));
        list.appendChild(el('dd', { text: health.current_running_sid || 'nothing' }));
        list.appendChild(el('dt', { text: 'Queued items' }));
        list.appendChild(el('dd', { text: String(health.queued_depth != null ? health.queued_depth : 0) }));
        root.appendChild(list);
    }

    // ------------------------------------------------------------------
    // Router
    // ------------------------------------------------------------------

    $(document).ready(function () {
        var setupRoot = document.getElementById('dsv-setup-root');
        var homeRoot = document.getElementById('dsv-home-root');
        var manageRoot = document.getElementById('dsv-manage-root');
        var healthRoot = document.getElementById('dsv-health-root');

        if (setupRoot) { initSetup(setupRoot); }
        if (homeRoot) { initHome(homeRoot); }
        if (manageRoot) { initManage(manageRoot); }
        if (healthRoot) { initHealth(healthRoot); }
    });
});
