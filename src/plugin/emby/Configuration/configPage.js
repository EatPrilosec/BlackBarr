define(['baseView', 'loading', 'emby-input', 'emby-button', 'emby-checkbox', 'emby-scroller'], function (BaseView, loading) {
    'use strict';

    const pluginId = 'e849b2c3-4d1a-429f-b78e-90f612d385a4';

    function loadPage(page, config) {
        page.querySelector('#txtBlackBarrUrl').value = config.BlackBarrUrl || 'http://127.0.0.1:6795';
        page.querySelector('#chkForceTranscodeCropped').checked = config.ForceTranscodeCropped !== false;
        page.querySelector('#chkForceTranscodeAll').checked = config.ForceTranscodeAll === true;
        loading.hide();
    }

    function onSubmit(e) {
        e.preventDefault();
        loading.show();

        const page = this;
        ApiClient.getPluginConfiguration(pluginId).then(function (config) {
            config.BlackBarrUrl = page.querySelector('#txtBlackBarrUrl').value;
            config.ForceTranscodeCropped = page.querySelector('#chkForceTranscodeCropped').checked;
            config.ForceTranscodeAll = page.querySelector('#chkForceTranscodeAll').checked;

            ApiClient.updatePluginConfiguration(pluginId, config).then(function (result) {
                loading.hide();
                Dashboard.processPluginConfigurationUpdateResult(result);
            });
        });

        return false;
    }

    function onTestConnection(e) {
        e.preventDefault();
        const page = document.querySelector('.view[data-controller="__plugin/blackbarrhelperjs"]') || document;
        const baseUrl = page.querySelector('#txtBlackBarrUrl').value.trim();
        loading.show();

        const testUrl = ApiClient.getUrl('BlackBarr/TestConnection') + '?Url=' + encodeURIComponent(baseUrl) + '&_v=' + Date.now();

        fetch(testUrl, {
            headers: {
                'X-Emby-Token': (typeof ApiClient !== 'undefined' && ApiClient.accessToken) ? ApiClient.accessToken() : ''
            }
        }).then(function (res) {
            return res.json();
        }).then(function (data) {
            loading.hide();
            if (data && (data.Success === true || data.success === true)) {
                Dashboard.alert('✅ SUCCESS: ' + (data.Message || data.message || 'Connected to BlackBarr API!'));
            } else {
                Dashboard.alert('❌ FAILED: ' + (data ? (data.Message || data.message || JSON.stringify(data)) : 'Unknown error'));
            }
        }).catch(function (err) {
            loading.hide();
            Dashboard.alert('❌ FAILED: ' + (err.message || err));
        });
    }

    function View(view, params) {
        BaseView.apply(this, arguments);

        view.querySelector('form').addEventListener('submit', onSubmit);
        const testBtn = view.querySelector('#btnTestConnection');
        if (testBtn) {
            testBtn.addEventListener('click', onTestConnection);
        }
    }

    Object.assign(View.prototype, BaseView.prototype);

    View.prototype.onResume = function (options) {
        BaseView.prototype.onResume.apply(this, arguments);
        loading.show();
        const page = this.view;

        ApiClient.getPluginConfiguration(pluginId).then(function (config) {
            loadPage(page, config);
        });
    };

    return View;
});
