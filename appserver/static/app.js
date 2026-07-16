/*
 * Data Source Validator - app.js
 *
 * Drives the three custom Simple XML views (setup.xml, home.xml,
 * health.xml) against this app's own REST endpoints (restmap.conf /
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

    var service = mvc.createService();
    var POLL_INTERVAL_MS = 4000;
    var HEALTH_POLL_INTERVAL_MS = 5000;

    function parseResponse(response) {
        var data = response && response.data;
        if (data && typeof data === 'object' && typeof data.payload === 'string') {
            try {
                return JSON.parse(data.payload);
            } catch (e) {
                return data.payload;
            }
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
        if (err.data && err.data.payload) {
            try {
                var parsed = JSON.parse(err.data.payload);
                if (parsed && parsed.error) { return parsed.error; }
            } catch (e) { /* fall through */ }
        }
        return err.message || String(err);
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
                message.textContent = 'Saved. The validation worker is now enabled.';
                message.className = 'dsv-form-message dsv-ok';
                passwordInput.value = '';
            }).fail(function (err) {
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
        $.when(
            restGet('datasource_validator/platforms'),
            restGet('datasource_validator/datasources')
        ).done(function (platformsResp, datasourcesResp) {
            renderHome(root, (platformsResp[0] || {}).platforms || [], (datasourcesResp[0] || {}).datasources || []);
        }).fail(function (err) {
            root.innerHTML = '';
            root.appendChild(el('p', { class: 'dsv-error', text: 'Could not load status: ' + errorText(err) }));
        });
    }

    function renderHome(root, platforms, datasources) {
        root.innerHTML = '';

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
                    el('td', {}, [statusBadge(ds.status, ds.status_label + (ds.status_stale ? ' (Stale)' : ''))]),
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
                statusEl.textContent = 'Lost track of run status: ' + errorText(err);
            });
        }
        tick();
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
        var healthRoot = document.getElementById('dsv-health-root');

        if (setupRoot) { initSetup(setupRoot); }
        if (homeRoot) { initHome(homeRoot); }
        if (healthRoot) { initHealth(healthRoot); }
    });
});
