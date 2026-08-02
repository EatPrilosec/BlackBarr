// BlackBarr Frontend Controller

let currentPage = 1;
const pageSize = 50;
let totalItems = 0;
let searchDebounceTimeout = null;
let scanStatusInterval = null;
let currentSortBy = 'updated_at';
let currentSortOrder = 'desc';

let selectedMediaIds = new Set();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        try { if (window.lucide) lucide.createIcons(); } catch(e) {}
        initApp();
    });
} else {
    try { if (window.lucide) lucide.createIcons(); } catch(e) {}
    initApp();
}

async function initApp() {
    setupEventListeners();
    await loadDirectories();
    await loadStats();
    await loadConfig();
    await loadMediaTable();
    startScanStatusPolling();
}

function setupEventListeners() {
    // Search input debounce
    document.getElementById('searchInput').addEventListener('input', (e) => {
        clearTimeout(searchDebounceTimeout);
        searchDebounceTimeout = setTimeout(() => {
            currentPage = 1;
            loadMediaTable();
        }, 300);
    });

    // Directory / Scan Path Filter
    const dirFilter = document.getElementById('directoryFilter');
    if (dirFilter) {
        dirFilter.addEventListener('change', () => {
            currentPage = 1;
            loadMediaTable();
        });
    }

    // Status Filter
    document.getElementById('statusFilter').addEventListener('change', () => {
        currentPage = 1;
        loadMediaTable();
    });

    // Format Filter (HDR / SDR)
    document.getElementById('formatFilter').addEventListener('change', () => {
        currentPage = 1;
        loadMediaTable();
    });

    // Column Header Sorting
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.getAttribute('data-sort');
            if (currentSortBy === col) {
                currentSortOrder = (currentSortOrder === 'asc') ? 'desc' : 'asc';
            } else {
                currentSortBy = col;
                currentSortOrder = (col === 'file_path' || col === 'status' || col === 'crop_val') ? 'asc' : 'desc';
            }
            updateSortHeaderIcons();
            currentPage = 1;
            loadMediaTable();
        });
    });

    // Refresh Button
    document.getElementById('btnRefresh').addEventListener('click', () => {
        loadStats();
        loadMediaTable();
        showToast('Refreshed data', 'info');
    });

    // Forced Transcoding Toggle
    document.getElementById('forcedTranscodeToggle').addEventListener('change', async (e) => {
        const enabled = e.target.checked ? "true" : "false";
        try {
            await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ configs: { force_transcode_enabled: enabled } })
            });
            showToast(`Forced transcoding ${enabled === "true" ? 'enabled' : 'disabled'}`, 'success');
        } catch (err) {
            showToast('Failed to update forced transcoding toggle', 'error');
        }
    });

    // Primary Run Scan Button (Default: Scan New Files)
    document.getElementById('btnTriggerScan').addEventListener('click', () => {
        triggerScanMode('new');
    });

    // Scan Dropdown Toggle
    const dropdownToggle = document.getElementById('btnScanDropdownToggle');
    const dropdownMenu = document.getElementById('scanDropdownMenu');
    
    if (dropdownToggle && dropdownMenu) {
        dropdownToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdownMenu.classList.toggle('hidden');
        });

        document.addEventListener('click', (e) => {
            if (!dropdownMenu.contains(e.target) && e.target !== dropdownToggle) {
                dropdownMenu.classList.add('hidden');
            }
        });
    }

    // Table Select All Checkbox
    const selectAllCb = document.getElementById('selectAllCheckbox');
    if (selectAllCb) {
        selectAllCb.addEventListener('change', (e) => {
            const isChecked = e.target.checked;
            document.querySelectorAll('.row-checkbox').forEach(cb => {
                cb.checked = isChecked;
                const id = parseInt(cb.getAttribute('data-id'));
                if (isChecked) {
                    selectedMediaIds.add(id);
                } else {
                    selectedMediaIds.delete(id);
                }
            });
            updateBatchActionBar();
        });
    }

    // Batch Rescan Selected Button
    document.getElementById('btnBatchRescan').addEventListener('click', async () => {
        if (selectedMediaIds.size === 0) return;
        try {
            const ids = Array.from(selectedMediaIds);
            const resp = await fetch('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'selected', media_ids: ids })
            });
            if (resp.ok) {
                showToast(`Rescan initiated for ${ids.length} selected items`, 'success');
                clearSelection();
                startScanStatusPolling();
            } else {
                const data = await resp.json();
                showToast(data.detail || 'Batch rescan failed', 'warning');
            }
        } catch (err) {
            showToast('Error triggering batch rescan', 'error');
        }
    });

    // Clear Selection Button
    document.getElementById('btnClearSelection').addEventListener('click', clearSelection);

    // Pagination
    document.getElementById('btnPrevPage').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            loadMediaTable();
        }
    });

    document.getElementById('btnNextPage').addEventListener('click', () => {
        if (currentPage * pageSize < totalItems) {
            currentPage++;
            loadMediaTable();
        }
    });

    // Add Manual Entry Modal
    document.getElementById('btnAddManual').addEventListener('click', () => {
        openMediaModal();
    });

    document.getElementById('btnCloseModal').addEventListener('click', closeMediaModal);
    document.getElementById('btnCancelModal').addEventListener('click', closeMediaModal);
    document.getElementById('mediaForm').addEventListener('submit', handleMediaFormSubmit);

    // Settings Modal
    document.getElementById('btnOpenSettings').addEventListener('click', openSettingsModal);
    document.getElementById('btnCloseSettingsModal').addEventListener('click', closeSettingsModal);
    document.getElementById('btnCancelSettings').addEventListener('click', closeSettingsModal);
    document.getElementById('settingsForm').addEventListener('submit', handleSettingsSubmit);

    // Test Connection Buttons
    document.getElementById('btnTestJellyfin').addEventListener('click', () => {
        testServerConnection('cfgTargetUrl', 'testStatusJellyfin', 'Jellyfin');
    });

    document.getElementById('btnTestEmby').addEventListener('click', () => {
        testServerConnection('cfgTargetEmbyUrl', 'testStatusEmby', 'Emby');
    });
}

async function loadDirectories() {
    try {
        const resp = await fetch('/api/directories');
        if (!resp.ok) return;
        const data = await resp.json();
        const select = document.getElementById('directoryFilter');
        if (!select || !data.directories) return;

        select.innerHTML = '<option value="">All Scan Paths</option>' + 
            data.directories.map(d => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join('');
    } catch (err) {
        console.error("Error loading scan directories:", err);
    }
}

async function loadStats() {
    try {
        const resp = await fetch('/api/stats');
        if (!resp.ok) return;
        const stats = await resp.json();

        document.getElementById('statTotal').innerText = stats.total || 0;
        document.getElementById('statCropped').innerText = stats.cropped || 0;
        document.getElementById('statNoBlackBars').innerText = stats.no_black_bars || 0;
        document.getElementById('statPending').innerText = stats.pending || 0;
        document.getElementById('statError').innerText = stats.error || 0;
    } catch (err) {
        console.error("Error loading stats:", err);
    }
}

async function loadConfig() {
    try {
        const resp = await fetch('/api/config');
        if (!resp.ok) return;
        const config = await resp.json();

        const forced = config.force_transcode_enabled === "true";
        document.getElementById('forcedTranscodeToggle').checked = forced;

        document.getElementById('cfgTargetUrl').value = config.target_server_url || "http://localhost:8096";
        document.getElementById('cfgTargetEmbyUrl').value = config.target_emby_url || "";
        document.getElementById('cfgScanDirectories').value = config.scan_directories || "/media";
        document.getElementById('cfgSdrLimit').value = config.sdr_crop_limit || "24";
        document.getElementById('cfgHdrLimit').value = config.hdr_crop_limit || "0.05";
        document.getElementById('cfgSampleCount').value = config.sample_count || "10";
        document.getElementById('cfgScanInterval').value = config.scan_interval_minutes || "60";
    } catch (err) {
        console.error("Error loading configuration:", err);
    }
}

async function loadMediaTable() {
    const search = document.getElementById('searchInput').value.trim();
    const status = document.getElementById('statusFilter').value;
    const format = document.getElementById('formatFilter').value;
    const pathPrefix = document.getElementById('directoryFilter') ? document.getElementById('directoryFilter').value : '';
    const offset = (currentPage - 1) * pageSize;

    const tbody = document.getElementById('mediaTableBody');
    
    try {
        const queryParams = {
            search: search,
            status: status,
            path_prefix: pathPrefix,
            sort_by: currentSortBy,
            sort_order: currentSortOrder,
            limit: pageSize,
            offset: offset
        };

        if (format === 'hdr') queryParams.is_hdr = 'true';
        if (format === 'sdr') queryParams.is_hdr = 'false';

        const query = new URLSearchParams(queryParams);
        const resp = await fetch(`/api/media?${query.toString()}`);
        if (!resp.ok) throw new Error("Failed to load media items");
        
        const data = await resp.json();
        totalItems = data.total;
        renderMediaTable(data.items);
        updatePaginationInfo();
    } catch (err) {
        console.error("Error loading media table:", err);
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="py-8 text-center text-rose-400">
                    Failed to connect to BlackBarr API server.
                </td>
            </tr>
        `;
    }
}

async function stopCurrentScan(event) {
    if (event) event.stopPropagation();
    try {
        const resp = await fetch('/api/scan/stop', { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            showToast(data.message || 'Scan cancellation requested', 'info');
        } else {
            showToast('Failed to stop scan', 'warning');
        }
    } catch (err) {
        showToast('Error stopping scan', 'error');
    }
}

async function triggerScanMode(mode) {
    const dropdownMenu = document.getElementById('scanDropdownMenu');
    if (dropdownMenu) dropdownMenu.classList.add('hidden');

    try {
        let payload = { mode: mode };
        if (mode === 'filtered') {
            payload.search = document.getElementById('searchInput').value.trim();
            payload.status = document.getElementById('statusFilter').value;
            payload.path_prefix = document.getElementById('directoryFilter') ? document.getElementById('directoryFilter').value : '';
            const fmt = document.getElementById('formatFilter').value;
            if (fmt === 'hdr') payload.is_hdr = true;
            if (fmt === 'sdr') payload.is_hdr = false;
        }

        const resp = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (resp.ok) {
            const data = await resp.json();
            showToast(data.message || 'Scan initiated', 'success');
            startScanStatusPolling();
        } else {
            const data = await resp.json();
            showToast(data.detail || 'Scan trigger failed', 'warning');
        }
    } catch (err) {
        showToast('Error triggering scan', 'error');
    }
}

async function rescanSingleMediaItem(mediaId, event) {
    if (event) event.stopPropagation();
    
    const btn = document.getElementById(`btn-rescan-${mediaId}`);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin text-indigo-400"></i>`;
        lucide.createIcons();
    }

    try {
        const resp = await fetch(`/api/media/${mediaId}/rescan`, { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            showToast('Item rescanned successfully', 'success');
            await loadStats();
            await loadMediaTable();
        } else {
            const data = await resp.json();
            showToast(data.detail || 'Rescan failed', 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>`;
                lucide.createIcons();
            }
        }
    } catch (err) {
        showToast('Error rescanning item', 'error');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>`;
            lucide.createIcons();
        }
    }
}

function handleRowCheckboxChange(id, isChecked) {
    if (isChecked) {
        selectedMediaIds.add(id);
    } else {
        selectedMediaIds.delete(id);
    }
    updateBatchActionBar();
}

function clearSelection() {
    selectedMediaIds.clear();
    const selectAllCb = document.getElementById('selectAllCheckbox');
    if (selectAllCb) selectAllCb.checked = false;
    document.querySelectorAll('.row-checkbox').forEach(cb => cb.checked = false);
    updateBatchActionBar();
}

function updateBatchActionBar() {
    const bar = document.getElementById('batchActionBar');
    const countSpan = document.getElementById('batchSelectedCount');
    if (!bar || !countSpan) return;

    if (selectedMediaIds.size > 0) {
        countSpan.innerText = `${selectedMediaIds.size} item${selectedMediaIds.size > 1 ? 's' : ''} selected`;
        bar.classList.remove('hidden');
    } else {
        bar.classList.add('hidden');
    }
}

function renderMediaTable(items) {
    const tbody = document.getElementById('mediaTableBody');
    if (!items || items.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="py-12 text-center text-slate-500">
                    No media files found matching current criteria.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = items.map(item => {
        const isCropped = item.crop_val && item.crop_val.trim() !== '';
        const hdrBadge = item.is_hdr 
            ? `<span class="px-2 py-0.5 text-[10px] font-bold rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">HDR</span>`
            : `<span class="px-2 py-0.5 text-[10px] font-bold rounded bg-slate-800 text-slate-400">SDR</span>`;

        let statusBadge = '';
        if (item.status === 'PROCESSED') {
            if (isCropped) {
                statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <i data-lucide="crop" class="w-3 h-3"></i> Cropped
                </span>`;
            } else {
                statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                    <i data-lucide="check" class="w-3 h-3"></i> No Black Bars
                </span>`;
            }
        } else if (item.status === 'PENDING') {
            statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <i data-lucide="loader-2" class="w-3 h-3 animate-spin"></i> Pending
            </span>`;
        } else if (item.status === 'ERROR') {
            statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                <i data-lucide="alert-triangle" class="w-3 h-3"></i> Error
            </span>`;
        } else {
            statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-400">
                ${item.status}
            </span>`;
        }

        const cropValDisplay = isCropped 
            ? `<code class="px-2 py-1 rounded bg-slate-900 border border-slate-800 text-emerald-400 font-mono text-xs">${escapeHtml(item.crop_val)}</code>`
            : `<span class="text-slate-500 text-xs italic">Full Frame</span>`;

        const isChecked = selectedMediaIds.has(item.id) ? 'checked' : '';

        return `
            <tr class="hover:bg-slate-900/50 transition-colors">
                <td class="py-3.5 px-4 text-center">
                    <input type="checkbox" data-id="${item.id}" class="row-checkbox rounded border-slate-700 bg-dark-base text-indigo-600 focus:ring-indigo-500 cursor-pointer" ${isChecked} onchange="handleRowCheckboxChange(${item.id}, this.checked)">
                </td>
                <td class="py-3.5 px-6 font-mono text-xs text-slate-200 break-all max-w-md">
                    ${escapeHtml(item.file_path)}
                </td>
                <td class="py-3.5 px-4">${hdrBadge}</td>
                <td class="py-3.5 px-4">${cropValDisplay}</td>
                <td class="py-3.5 px-4">${statusBadge}</td>
                <td class="py-3.5 px-4 text-xs text-slate-400">${item.updated_at || 'N/A'}</td>
                <td class="py-3.5 px-6 text-right">
                    <div class="flex items-center justify-end gap-2">
                        <button id="btn-rescan-${item.id}" onclick="rescanSingleMediaItem(${item.id}, event)" class="p-1.5 rounded-lg bg-slate-800 hover:bg-indigo-900/40 text-slate-300 hover:text-indigo-400 transition-all" title="Rescan File">
                            <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
                        </button>
                        <button onclick="editMediaItem(${item.id})" class="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-all" title="Edit Entry">
                            <i data-lucide="edit-3" class="w-3.5 h-3.5"></i>
                        </button>
                        <button onclick="deleteMediaItem(${item.id})" class="p-1.5 rounded-lg bg-slate-800 hover:bg-rose-900/40 text-slate-300 hover:text-rose-400 transition-all" title="Delete Entry">
                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    lucide.createIcons();
    updateBatchActionBar();
}

function updatePaginationInfo() {
    const start = totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1;
    const end = Math.min(currentPage * pageSize, totalItems);
    document.getElementById('paginationInfo').innerText = `Showing ${start}-${end} of ${totalItems} items`;

    document.getElementById('btnPrevPage').disabled = (currentPage <= 1);
    document.getElementById('btnNextPage').disabled = (currentPage * pageSize >= totalItems);
}

let wasScanning = false;

function startScanStatusPolling() {
    if (scanStatusInterval) clearInterval(scanStatusInterval);

    scanStatusInterval = setInterval(async () => {
        try {
            const resp = await fetch('/api/scan/status');
            if (!resp.ok) return;
            const status = await resp.json();

            const badge = document.getElementById('scannerProgressBadge');
            const txt = document.getElementById('scannerStatusText');

            if (status.is_scanning) {
                wasScanning = true;
                badge.classList.remove('hidden');
                badge.classList.add('flex');
                txt.innerText = `Scanning (${status.scanned_files}/${status.total_files})`;
            } else {
                badge.classList.add('hidden');
                badge.classList.remove('flex');
                if (wasScanning) {
                    wasScanning = false;
                    loadStats();
                    loadMediaTable();
                }
            }
        } catch (err) {
            console.error("Scanner status check error:", err);
        }
    }, 2500);
}

function openMediaModal(item = null) {
    const modal = document.getElementById('mediaModal');
    const form = document.getElementById('mediaForm');
    form.reset();

    if (item) {
        document.getElementById('modalTitle').innerText = 'Edit Crop Entry';
        document.getElementById('modalMediaId').value = item.id;
        document.getElementById('modalFilePath').value = item.file_path;
        document.getElementById('modalCropVal').value = item.crop_val || '';
        document.getElementById('modalStatus').value = item.status;
        document.getElementById('modalIsHdr').checked = Boolean(item.is_hdr);
    } else {
        document.getElementById('modalTitle').innerText = 'Add Manual Entry';
        document.getElementById('modalMediaId').value = '';
        document.getElementById('modalStatus').value = 'PROCESSED';
    }

    modal.classList.remove('hidden');
}

function closeMediaModal() {
    document.getElementById('mediaModal').classList.add('hidden');
}

async function editMediaItem(id) {
    try {
        const resp = await fetch(`/api/media?limit=1000`);
        const data = await resp.json();
        const item = data.items.find(i => i.id === id);
        if (item) {
            openMediaModal(item);
        }
    } catch (err) {
        showToast('Error loading media item details', 'error');
    }
}

async function deleteMediaItem(id) {
    if (!confirm('Are you sure you want to delete this media crop entry?')) return;
    try {
        const resp = await fetch(`/api/media/${id}`, { method: 'DELETE' });
        if (resp.ok) {
            showToast('Media entry deleted', 'success');
            loadStats();
            loadMediaTable();
        } else {
            showToast('Failed to delete media entry', 'error');
        }
    } catch (err) {
        showToast('Error deleting entry', 'error');
    }
}

async function handleMediaFormSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('modalMediaId').value;
    const filePath = document.getElementById('modalFilePath').value.trim();
    const cropVal = document.getElementById('modalCropVal').value.trim() || null;
    const status = document.getElementById('modalStatus').value;
    const isHdr = document.getElementById('modalIsHdr').checked;

    try {
        let resp;
        if (id) {
            resp = await fetch(`/api/media/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ crop_val: cropVal, status: status, is_hdr: isHdr })
            });
        } else {
            resp = await fetch('/api/media', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: filePath, crop_val: cropVal, status: status, is_hdr: isHdr })
            });
        }

        if (resp.ok) {
            showToast('Media item saved successfully', 'success');
            closeMediaModal();
            loadStats();
            loadMediaTable();
        } else {
            showToast('Failed to save media item', 'error');
        }
    } catch (err) {
        showToast('Error submitting media form', 'error');
    }
}

function openSettingsModal() {
    document.getElementById('settingsModal').classList.remove('hidden');
}

function closeSettingsModal() {
    document.getElementById('settingsModal').classList.add('hidden');
}

async function handleSettingsSubmit(e) {
    e.preventDefault();
    const configs = {
        target_server_url: document.getElementById('cfgTargetUrl').value.trim(),
        target_emby_url: document.getElementById('cfgTargetEmbyUrl').value.trim(),
        scan_directories: document.getElementById('cfgScanDirectories').value.trim(),
        sdr_crop_limit: document.getElementById('cfgSdrLimit').value.trim(),
        hdr_crop_limit: document.getElementById('cfgHdrLimit').value.trim(),
        sample_count: document.getElementById('cfgSampleCount').value.trim(),
        scan_interval_minutes: document.getElementById('cfgScanInterval').value.trim()
    };

    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ configs: configs })
        });
        if (resp.ok) {
            showToast('Configuration updated successfully', 'success');
            closeSettingsModal();
        } else {
            showToast('Failed to update configuration', 'error');
        }
    } catch (err) {
        showToast('Error updating configuration', 'error');
    }
}

async function testServerConnection(inputId, statusId, serverLabel) {
    const url = document.getElementById(inputId).value.trim();
    const statusEl = document.getElementById(statusId);
    
    if (!url) {
        statusEl.className = "text-xs mt-1 font-medium text-amber-400";
        statusEl.innerText = `Please enter a ${serverLabel} URL first.`;
        statusEl.classList.remove('hidden');
        return;
    }

    statusEl.className = "text-xs mt-1 font-medium text-slate-400";
    statusEl.innerText = `Testing connection to ${serverLabel}...`;
    statusEl.classList.remove('hidden');

    try {
        const resp = await fetch('/api/test-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url })
        });
        const data = await resp.json();

        if (resp.ok && data.success) {
            statusEl.className = "text-xs mt-1 font-medium text-emerald-400";
            statusEl.innerText = `✓ ${data.message}`;
            showToast(`${serverLabel} connection test successful!`, 'success');
        } else {
            statusEl.className = "text-xs mt-1 font-medium text-rose-400";
            statusEl.innerText = `✗ ${data.message || 'Connection failed'}`;
            showToast(`${serverLabel} connection test failed`, 'error');
        }
    } catch (err) {
        statusEl.className = "text-xs mt-1 font-medium text-rose-400";
        statusEl.innerText = `✗ Connection error: ${err.message}`;
        showToast(`Failed to test ${serverLabel} connection`, 'error');
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    
    let bg = 'bg-slate-800 text-slate-200 border-slate-700';
    if (type === 'success') bg = 'bg-emerald-900/90 text-emerald-200 border-emerald-700';
    if (type === 'error') bg = 'bg-rose-900/90 text-rose-200 border-rose-700';
    if (type === 'warning') bg = 'bg-amber-900/90 text-amber-200 border-amber-700';

    toast.className = `toast-enter pointer-events-auto px-4 py-3 rounded-xl border shadow-xl text-xs font-semibold flex items-center gap-2 ${bg}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;

    container.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, 3500);
}

function updateSortHeaderIcons() {
    const columns = ['file_path', 'is_hdr', 'crop_val', 'status', 'updated_at'];
    columns.forEach(col => {
        const iconEl = document.getElementById(`sort_${col}`);
        if (!iconEl) return;
        if (currentSortBy === col) {
            iconEl.innerText = (currentSortOrder === 'asc') ? '↑' : '↓';
            iconEl.className = 'sort-icon text-[10px] text-indigo-400 font-bold';
        } else {
            iconEl.innerText = '↕';
            iconEl.className = 'sort-icon text-[10px] text-slate-500';
        }
    });
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>"']/g, function(m) {
        return {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        }[m];
    });
}
