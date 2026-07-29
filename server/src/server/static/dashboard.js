import { runAction } from './actions.js';
import { openDeployDialog } from './deploy.js';
import { formatSize, timeAgo } from './format.js';
import { request } from './http.js';

const DOMAIN = document.getElementById('dashboard-root').dataset.domain;

function esc(str) {
    const element = document.createElement('div');
    element.textContent = str;
    return element.innerHTML;
}

function siteUrl(name) {
    if (DOMAIN && DOMAIN !== 'localhost:8080') return 'https://' + name + '.' + DOMAIN;
    return 'http://' + name + '.localhost:8080';
}

function emptyState(title, description, ctaHtml) {
    return `<div class="flex flex-col items-start gap-2 px-5 py-8">
        <h3 class="text-xl font-bold">${title}</h3>
        <p class="max-w-md text-rule">${description}</p>
        ${ctaHtml || ''}
    </div>`;
}

async function loadSites() {
    const container = document.getElementById('sites-container');
    try {
        const sites = await request('/sites');
        document.getElementById('stat-sites').textContent = sites.length;
        const totalBytes = sites.reduce((sum, site) => sum + (site.size_bytes || 0), 0);
        document.getElementById('stat-size').textContent = formatSize(totalBytes);
        if (!sites.length) {
            container.innerHTML = emptyState('No sites yet', 'Reload to see how to deploy one.');
            return;
        }

        let html = '<div class="overflow-x-auto"><table class="table"><thead><tr>';
        html += '<th>Name</th><th>Visibility</th><th>Last deployed</th><th>Total Views</th><th class="text-right">Actions</th>';
        html += '</tr></thead><tbody>';
        for (const site of sites) {
            const url = siteUrl(site.name);
            const name = esc(site.name);
            html += '<tr>';
            html += '<td><a href="/dashboard/sites/' + encodeURIComponent(site.name) + '" class="link">' + name + '</a></td>';
            html += '<td>' + window.visibilityBadge(Boolean(site.private)) + '</td>';
            html += '<td class="text-rule">' + timeAgo(site.created) + '</td>';
            html += '<td class="tabular-nums text-rule">' + (site.total_views || 0).toLocaleString() + '</td>';
            html += '<td class="text-right"><a href="' + esc(url) + '" target="_blank" class="btn-sm-outline">View live</a></td>';
            html += '</tr>';
        }
        container.innerHTML = html + '</tbody></table></div>';
    } catch {
        container.innerHTML = emptyState('Failed to load sites', 'Something went wrong. Please refresh the page.');
    }
}

const deleteTokenDialog = document.getElementById('delete-token-dialog');
const closeDeleteToken = () => window.BuzzDialogs.close(deleteTokenDialog);
document.getElementById('cancel-delete-token').addEventListener('click', closeDeleteToken);
window.BuzzDialogs.onCancel(deleteTokenDialog, closeDeleteToken);

function confirmDeleteToken(tokenId) {
    const button = document.getElementById('confirm-delete-token');
    const newButton = button.cloneNode(true);
    button.parentNode.replaceChild(newButton, button);
    const error = document.getElementById('delete-token-error');
    error.classList.add('hidden');
    newButton.addEventListener('click', () => runAction(newButton, 'Revoking...', async () => {
        try {
            await request('/tokens/' + encodeURIComponent(tokenId), { method: 'DELETE' }, 'Buzz could not revoke this token.');
            window.BuzzDialogs.close(deleteTokenDialog);
            await loadTokens();
        } catch (requestError) {
            error.textContent = requestError.message;
            error.classList.remove('hidden');
        }
    }));
    window.BuzzDialogs.open(deleteTokenDialog, document.activeElement);
}

async function loadTokens() {
    const container = document.getElementById('tokens-container');
    try {
        const tokens = await request('/tokens');
        document.getElementById('stat-tokens').textContent = tokens.length;
        if (!tokens.length) {
            container.innerHTML = emptyState('No deploy tokens', 'Create tokens via the CLI for CI/CD deployments.');
            return;
        }

        let html = '<div class="overflow-x-auto"><table class="table"><thead><tr>';
        html += '<th>Name</th><th>Site</th><th>Created</th><th>Last Used</th><th class="text-right">Actions</th>';
        html += '</tr></thead><tbody>';
        for (const token of tokens) {
            html += '<tr>';
            html += '<td class="font-bold">' + esc(token.name) + '</td>';
            html += '<td><span class="badge-outline">' + esc(token.site_name) + '</span></td>';
            html += '<td class="text-rule">' + timeAgo(token.created_at) + '</td>';
            html += '<td class="text-rule">' + (token.last_used_at ? timeAgo(token.last_used_at) : 'Never') + '</td>';
            html += '<td class="text-right"><button class="btn-sm-outline border-danger text-danger-dark" data-token="' + esc(token.id) + '">Revoke</button></td>';
            html += '</tr>';
        }
        container.innerHTML = html + '</tbody></table></div>';
        container.querySelectorAll('[data-token]').forEach(button => {
            button.addEventListener('click', () => confirmDeleteToken(button.dataset.token));
        });
    } catch {
        container.innerHTML = emptyState('Failed to load tokens', 'Something went wrong. Please refresh the page.');
    }
}

window.onDeploySuccess = () => loadSites();

function initFirstRun() {
    const deployButton = document.getElementById('first-run-deploy');
    if (!deployButton) return;
    deployButton.addEventListener('click', () => openDeployDialog());
    let deployed = false;
    window.onDeploySuccess = () => { deployed = true; };
    document.getElementById('deploy-dialog').addEventListener('close', () => {
        if (deployed) location.reload();
    });
}

document.getElementById('deploy-site')?.addEventListener('click', () => openDeployDialog());
if (document.getElementById('sites-container')) {
    loadSites();
    loadTokens();
}
initFirstRun();
