import { runAction } from './actions.js';
import { openDeployDialog } from './deploy.js';
import { formatSize, timeAgo } from './format.js';
import { request } from './http.js';

const root = document.getElementById('site-detail-root');
const SITE_NAME = root.dataset.siteName;
let analyticsChart;

window.onDeploySuccess = () => setTimeout(() => location.reload(), 800);

function esc(str) {
    const element = document.createElement('div');
    element.textContent = str;
    return element.innerHTML;
}

function domainRequest(path, options = {}) {
    return request(
        '/sites/' + encodeURIComponent(SITE_NAME) + '/domains' + path,
        options,
        'Buzz could not update this custom domain.',
    );
}

function showClaimError(button, message) {
    const error = button.closest('[data-domain-claim]').querySelector('.claim-action-error');
    error.textContent = message;
    error.classList.remove('hidden');
}

async function copyDomainValue(button) {
    const target = document.getElementById(button.dataset.copyTarget);
    try {
        await navigator.clipboard.writeText(target.textContent.trim());
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = 'Copy'; }, 1500);
    } catch {
        showClaimError(button, 'Could not copy the DNS value. Select it and copy it manually.');
    }
}

function initBuzzAccess() {
    const trigger = document.getElementById('visibility-trigger');
    const popover = document.getElementById('visibility-popover');
    const error = document.getElementById('visibility-error');

    popover.addEventListener('beforetoggle', event => {
        if (event.newState !== 'open') return;
        error.classList.add('hidden');
        const box = trigger.getBoundingClientRect();
        popover.style.top = (box.bottom + 8) + 'px';
        popover.style.right = Math.max(8, document.documentElement.clientWidth - box.right) + 'px';
    });

    popover.querySelectorAll('.visibility-option').forEach(option => {
        option.addEventListener('click', async () => {
            const wantsPrivate = option.dataset.private === 'true';
            if (option.getAttribute('aria-current') === 'true') {
                popover.hidePopover();
                return;
            }
            const options = popover.querySelectorAll('.visibility-option');
            options.forEach(item => { item.disabled = true; });
            try {
                await request('/sites/' + encodeURIComponent(SITE_NAME) + '/access', {
                    method: wantsPrivate ? 'PUT' : 'DELETE',
                }, 'Buzz could not change who can view this site.');
                location.reload();
            } catch (failure) {
                error.textContent = failure.message;
                error.classList.remove('hidden');
                options.forEach(item => { item.disabled = false; });
            }
        });
    });

    const manageButton = document.getElementById('manage-access');
    const manageDialog = document.getElementById('manage-access-dialog');
    if (!manageButton || !manageDialog) return;

    const addDialog = document.getElementById('add-reader-dialog');
    const addForm = document.getElementById('add-reader-form');
    const loginInput = document.getElementById('reader-login');
    const resolved = document.getElementById('resolved-reader');
    const confirmAdd = document.getElementById('confirm-add-reader');
    const findReader = document.getElementById('find-reader');
    const addError = document.getElementById('add-reader-error');
    let resolvedLogin = null;
    let readerRequestVersion = 0;

    function closeManageAccess() {
        window.BuzzDialogs.close(manageDialog, { restore: false });
        popover.showPopover();
        manageButton.focus();
    }

    manageButton.addEventListener('click', () => {
        popover.hidePopover();
        window.BuzzDialogs.open(manageDialog, manageButton);
    });
    document.getElementById('close-manage-access').addEventListener('click', closeManageAccess);
    window.BuzzDialogs.onCancel(manageDialog, closeManageAccess);

    const openAddReader = document.getElementById('open-add-reader');
    openAddReader.addEventListener('click', () => {
        addForm.reset();
        resetResolvedReader();
        window.BuzzDialogs.openChild(manageDialog, addDialog, openAddReader);
        loginInput.focus();
    });
    const closeAddReader = () => {
        resetResolvedReader();
        window.BuzzDialogs.closeChild(addDialog);
    };
    document.getElementById('cancel-add-reader').addEventListener('click', closeAddReader);
    window.BuzzDialogs.onCancel(addDialog, closeAddReader);

    function resetResolvedReader() {
        readerRequestVersion += 1;
        resolvedLogin = null;
        resolved.classList.add('hidden');
        confirmAdd.classList.add('hidden');
        findReader.classList.remove('hidden');
        confirmAdd.disabled = false;
        findReader.disabled = false;
        addError.classList.add('hidden');
    }
    loginInput.addEventListener('input', resetResolvedReader);

    addForm.addEventListener('submit', async event => {
        event.preventDefault();
        addError.classList.add('hidden');
        const login = loginInput.value.trim();
        const granting = resolvedLogin === login;
        const submit = granting ? confirmAdd : findReader;
        const requestVersion = ++readerRequestVersion;
        submit.disabled = true;
        try {
            const base = '/sites/' + encodeURIComponent(SITE_NAME) + '/access';
            const data = await request(
                granting ? base + '/readers' : base + '/github-users/' + encodeURIComponent(login),
                granting ? {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ github_login: login }),
                } : {},
                'Buzz could not find that GitHub user.',
            );
            if (granting) {
                location.reload();
                return;
            }
            if (requestVersion !== readerRequestVersion || loginInput.value.trim() !== login) return;
            resolvedLogin = data.github_login;
            loginInput.value = data.github_login;
            document.getElementById('resolved-reader-avatar').src = data.avatar_url || 'https://github.com/' + data.github_login + '.png?size=96';
            document.getElementById('resolved-reader-name').textContent = data.github_name || data.github_login;
            document.getElementById('resolved-reader-login').textContent = '@' + data.github_login;
            resolved.classList.remove('hidden');
            confirmAdd.classList.remove('hidden');
            findReader.classList.add('hidden');
        } catch (requestError) {
            if (requestVersion !== readerRequestVersion) return;
            addError.textContent = requestError.message;
            addError.classList.remove('hidden');
        } finally {
            if (requestVersion === readerRequestVersion) submit.disabled = false;
        }
    });

    const removeDialog = document.getElementById('remove-reader-dialog');
    const confirmRemove = document.getElementById('confirm-remove-reader');
    const removeError = document.getElementById('remove-reader-error');
    let readerToRemove = null;
    document.querySelectorAll('.remove-access-reader').forEach(button => {
        button.addEventListener('click', () => {
            readerToRemove = button;
            document.getElementById('remove-reader-login').textContent = '@' + button.dataset.readerLogin;
            removeError.classList.add('hidden');
            window.BuzzDialogs.openChild(manageDialog, removeDialog, button);
        });
    });
    const closeRemoveReader = () => window.BuzzDialogs.closeChild(removeDialog);
    document.getElementById('cancel-remove-reader').addEventListener('click', closeRemoveReader);
    window.BuzzDialogs.onCancel(removeDialog, closeRemoveReader);
    confirmRemove.addEventListener('click', async () => {
        if (!readerToRemove) return;
        confirmRemove.disabled = true;
        removeError.classList.add('hidden');
        try {
            await request(
                '/sites/' + encodeURIComponent(SITE_NAME) + '/access/readers/' + encodeURIComponent(readerToRemove.dataset.readerId),
                { method: 'DELETE' },
                'Buzz could not remove access.',
            );
            location.reload();
        } catch (requestError) {
            removeError.textContent = requestError.message;
            removeError.classList.remove('hidden');
            confirmRemove.disabled = false;
        }
    });
}

function initCustomDomains() {
    document.querySelectorAll('.copy-domain-value').forEach(button => {
        button.addEventListener('click', () => copyDomainValue(button));
    });

    const dialog = document.getElementById('add-domain-dialog');
    const openButton = document.getElementById('open-domain-dialog');
    const cancelButton = document.getElementById('cancel-domain-dialog');
    const form = document.getElementById('add-domain-form');
    if (dialog && openButton && cancelButton && form) {
        const closeDialog = () => window.BuzzDialogs.close(dialog);
        openButton.addEventListener('click', () => window.BuzzDialogs.open(dialog, openButton));
        cancelButton.addEventListener('click', closeDialog);
        window.BuzzDialogs.onCancel(dialog, closeDialog);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const submit = document.getElementById('submit-domain');
            const error = document.getElementById('add-domain-error');
            submit.disabled = true;
            error.classList.add('hidden');
            try {
                await domainRequest('', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hostname: form.elements.hostname.value }),
                });
                location.reload();
            } catch (requestError) {
                error.textContent = requestError.message;
                error.classList.remove('hidden');
                submit.disabled = false;
            }
        });
    }

    document.querySelectorAll('.check-domain').forEach(button => {
        button.addEventListener('click', () => runAction(button, 'Checking...', async () => {
            const error = button.closest('[data-domain-claim]').querySelector('.claim-action-error');
            error.classList.add('hidden');
            try {
                await domainRequest('/' + button.dataset.claimId + '/check', { method: 'POST' });
                location.reload();
            } catch (requestError) {
                showClaimError(button, requestError.message);
            }
        }));
    });

    document.querySelectorAll('.transition-action').forEach(button => {
        button.addEventListener('click', () => runAction(
            button,
            button.dataset.action === 'retry' ? 'Retrying...' : 'Cancelling...',
            async () => {
                const error = button.closest('[data-domain-claim]').querySelector('.claim-action-error');
                error.classList.add('hidden');
                try {
                    await domainRequest('/' + button.dataset.claimId + '/transition/' + button.dataset.action, { method: 'POST' });
                    location.reload();
                } catch (requestError) {
                    error.textContent = requestError.message;
                    error.classList.remove('hidden');
                }
            },
        ));
    });

    const removeDialog = document.getElementById('remove-domain-dialog');
    const cancelRemove = document.getElementById('cancel-remove-domain');
    const confirmRemove = document.getElementById('confirm-remove-domain');
    let removeButton;
    if (removeDialog && cancelRemove && confirmRemove) {
        const closeRemoveDialog = () => window.BuzzDialogs.close(removeDialog);
        cancelRemove.addEventListener('click', closeRemoveDialog);
        window.BuzzDialogs.onCancel(removeDialog, closeRemoveDialog);
        document.querySelectorAll('.remove-domain').forEach(button => {
            button.addEventListener('click', () => {
                removeButton = button;
                document.getElementById('remove-domain-hostname').textContent = button.dataset.hostname;
                document.getElementById('remove-domain-error').classList.add('hidden');
                window.BuzzDialogs.open(removeDialog, button);
            });
        });
        confirmRemove.addEventListener('click', async () => {
            if (!removeButton) return;
            confirmRemove.disabled = true;
            try {
                await domainRequest('/' + removeButton.dataset.claimId, { method: 'DELETE' });
                location.reload();
            } catch (requestError) {
                const removeError = document.getElementById('remove-domain-error');
                removeError.textContent = requestError.message;
                removeError.classList.remove('hidden');
                confirmRemove.disabled = false;
            }
        });
    }
}

function renderDimension(title, rows) {
    if (!rows?.length) return '';
    let html = '<div class="break-inside-avoid pb-5"><div class="border-2 border-ink"><div class="border-b-2 border-ink px-4 py-2.5 font-bold">' + title + '</div>';
    html += '<div class="divide-y-2 divide-ink">';
    for (const row of rows) {
        html += '<div class="flex items-center justify-between gap-4 px-4 py-2.5">';
        html += '<span class="truncate text-rule">' + esc(row.value) + '</span>';
        html += '<span class="font-bold tabular-nums">' + row.views.toLocaleString() + '</span>';
        html += '</div>';
    }
    return html + '</div></div></div>';
}

function renderChart(series) {
    const canvas = document.getElementById('analytics-chart');
    const empty = document.getElementById('analytics-chart-empty');
    const max = Math.max(...series.map(day => Math.max(day.views, day.visitors)), 0);
    if (analyticsChart) {
        analyticsChart.destroy();
        analyticsChart = null;
    }
    if (max === 0) {
        canvas.classList.add('hidden');
        empty.classList.remove('hidden');
        empty.classList.add('flex');
        return;
    }
    if (!window.Chart) throw new Error('Chart.js failed to load');
    canvas.classList.remove('hidden');
    empty.classList.add('hidden');
    empty.classList.remove('flex');
    window.Chart.defaults.font.family = 'Arial, sans-serif';
    window.Chart.defaults.color = '#0b0c0c';
    const labels = series.map(day => new Date(day.day + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }));
    analyticsChart = new window.Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Views',
                    data: series.map(day => day.views),
                    borderColor: '#1d70b8',
                    backgroundColor: '#1d70b8',
                    tension: 0,
                    fill: false,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                },
                {
                    label: 'Visitors',
                    data: series.map(day => day.visitors),
                    borderColor: '#505a5f',
                    backgroundColor: '#505a5f',
                    tension: 0,
                    fill: false,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8 },
                },
                tooltip: {
                    callbacks: {
                        title: items => series[items[0].dataIndex].day,
                        label: item => item.dataset.label + ': ' + item.parsed.y.toLocaleString(),
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } },
                y: {
                    beginAtZero: true,
                    suggestedMax: Math.max(max, 5),
                    ticks: { precision: 0 },
                },
            },
        },
    });
}

let breakdownHasData = false;

function updateBreakdownEmpty() {
    document.getElementById('analytics-breakdown-empty')?.classList.toggle('hidden', breakdownHasData);
}

function initDisclosure(id, storageKey) {
    const element = document.getElementById(id);
    try {
        if (localStorage.getItem(storageKey) === '1') element.open = true;
    } catch {}
    element.addEventListener('toggle', () => {
        try {
            localStorage.setItem(storageKey, element.open ? '1' : '0');
        } catch {}
    });
}

async function loadAnalytics() {
    try {
        const data = await request('/dashboard/sites/' + encodeURIComponent(SITE_NAME) + '/analytics');
        document.getElementById('analytics-views').textContent = data.totals.views.toLocaleString();
        document.getElementById('analytics-visitors').textContent = data.totals.visitors.toLocaleString();
        document.getElementById('analytics-bytes').textContent = formatSize(data.totals.bytes);
        document.getElementById('analytics-not-found').textContent = data.totals.not_found.toLocaleString();
        renderChart(data.series);
        const dimensions = document.getElementById('analytics-dimensions');
        dimensions.innerHTML = renderDimension('Top pages', data.top_pages)
            + renderDimension('Referrers', data.referrers)
            + renderDimension('Campaigns', data.campaigns)
            + renderDimension('Countries', data.countries);
        const hasDimensions = dimensions.innerHTML !== '';
        dimensions.classList.toggle('hidden', !hasDimensions);
        if (hasDimensions) breakdownHasData = true;
        updateBreakdownEmpty();
    } catch {
        document.getElementById('analytics-container').innerHTML = '<div class="px-4 py-8 text-center text-rule">Failed to load analytics.</div>';
    }
}

function initDeleteSite() {
    const dialog = document.getElementById('delete-site-dialog');
    const openButton = document.getElementById('open-delete-site');
    const closeDialog = () => window.BuzzDialogs.close(dialog);
    openButton.addEventListener('click', () => window.BuzzDialogs.open(dialog, openButton));
    document.getElementById('cancel-delete-site').addEventListener('click', closeDialog);
    window.BuzzDialogs.onCancel(dialog, closeDialog);
    const confirm = document.getElementById('confirm-delete-site');
    confirm.addEventListener('click', () => runAction(confirm, 'Deleting...', async () => {
        const error = document.getElementById('delete-site-error');
        error.classList.add('hidden');
        try {
            await request('/sites/' + encodeURIComponent(SITE_NAME), { method: 'DELETE' }, 'Could not delete this site.');
            window.location.href = '/';
        } catch (requestError) {
            error.textContent = requestError.message;
            error.classList.remove('hidden');
        }
    }));
}

document.getElementById('redeploy-site').addEventListener('click', () => openDeployDialog(SITE_NAME));
document.getElementById('site-deployed-at').textContent = timeAgo(root.dataset.createdAt);
document.querySelectorAll('[data-relative-time]').forEach(element => {
    element.textContent = timeAgo(element.dataset.relativeTime);
});
initBuzzAccess();
initCustomDomains();
loadAnalytics();
initDisclosure('analytics-details', 'buzz:' + SITE_NAME + ':breakdown');
initDisclosure('files-details', 'buzz:' + SITE_NAME + ':files');
initDeleteSite();
