import { formatSize } from './format.js';

const IGNORED_DIR_NAMES = new Set(['.git', 'node_modules', '.vscode', '.idea']);
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;
const WARN_UPLOAD_BYTES = 50 * 1024 * 1024;

function esc(str) {
    const element = document.createElement('div');
    element.textContent = str;
    return element.innerHTML;
}

function isIgnoredPath(path) {
    const base = path.split('/').pop() || '';
    if (base === '.DS_Store') return true;
    if (base === '.env' || /^\.env\./.test(base)) return true;
    return path.split('/').some(segment => IGNORED_DIR_NAMES.has(segment));
}

function setUploadStatus(variant, html) {
    const box = document.getElementById('upload-status');
    box.classList.remove('hidden');
    if (variant === 'error') {
        box.innerHTML = '<div class="alert-destructive" role="alert"><h2>There is a problem</h2><p class="mt-2">' + html + '</p></div>';
    } else if (variant === 'success') {
        box.innerHTML = '<div class="bg-success p-5 text-paper" role="status">' + html + '</div>';
    } else {
        box.innerHTML = '<div class="border-2 border-ink bg-mist px-4 py-3">' + html + '</div>';
    }
}

function clearUploadStatus() {
    const box = document.getElementById('upload-status');
    box.classList.add('hidden');
    box.innerHTML = '';
}

function progressMarkup(label, percent) {
    const value = Math.max(0, Math.min(100, Math.round(percent)));
    return '<div class="flex items-center justify-between gap-3"><span>' + esc(label) + '</span><span class="tabular-nums text-rule">' + value + '%</span></div>' +
        '<div class="mt-2 h-2 w-full border border-ink bg-paper"><div class="h-full bg-ink" style="width:' + value + '%"></div></div>';
}

function zipOptions(onPercent) {
    return {
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 9 },
        onUpdate: onPercent ? metadata => onPercent(metadata.percent) : undefined,
    };
}

function readEntriesBatch(reader) {
    return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function entryToFile(entry) {
    return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walkEntry(entry, zip, prefix) {
    if (entry.isFile) {
        const path = prefix ? prefix + '/' + entry.name : entry.name;
        if (isIgnoredPath(path)) return;
        zip.file(path, await entryToFile(entry));
        return;
    }
    if (entry.isDirectory) {
        if (IGNORED_DIR_NAMES.has(entry.name)) return;
        const nextPrefix = prefix ? prefix + '/' + entry.name : entry.name;
        const reader = entry.createReader();
        while (true) {
            const batch = await readEntriesBatch(reader);
            if (!batch.length) break;
            for (const child of batch) await walkEntry(child, zip, nextPrefix);
        }
    }
}

async function zipFromEntries(entries) {
    const zip = new window.JSZip();
    const stripRoot = entries.length === 1 && entries[0].isDirectory;
    if (stripRoot) {
        if (IGNORED_DIR_NAMES.has(entries[0].name)) throw new Error('That folder is in the ignore list.');
        const reader = entries[0].createReader();
        while (true) {
            const batch = await readEntriesBatch(reader);
            if (!batch.length) break;
            for (const child of batch) await walkEntry(child, zip, '');
        }
    } else {
        for (const entry of entries) await walkEntry(entry, zip, '');
    }
    return zip;
}

function zipFromRelativeFiles(files) {
    const zip = new window.JSZip();
    const paths = files.map(file => file.webkitRelativePath || file.name);
    const firstSegment = paths[0].split('/')[0];
    const stripRoot = paths.length > 0 && paths.every(path => path.split('/')[0] === firstSegment && path.includes('/'));
    for (const file of files) {
        let path = file.webkitRelativePath || file.name;
        if (stripRoot) path = path.substring(firstSegment.length + 1);
        if (!isIgnoredPath(path)) zip.file(path, file);
    }
    return zip;
}

function zipFromLooseFiles(files) {
    const zip = new window.JSZip();
    for (const file of files) {
        if (!isIgnoredPath(file.name)) zip.file(file.name, file);
    }
    return zip;
}

async function generateZipBlob(zip, label) {
    return zip.generateAsync(zipOptions(percent => {
        setUploadStatus('progress', progressMarkup(label + '...', percent));
    }));
}

function uploadBlob(blob, subdomain, makePrivate) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/deploy');
        if (subdomain) xhr.setRequestHeader('x-buzz-site', subdomain);
        if (makePrivate) xhr.setRequestHeader('x-buzz-access', 'private');
        xhr.upload.addEventListener('progress', event => {
            if (!event.lengthComputable) return;
            setUploadStatus('progress', progressMarkup('Uploading...', (event.loaded / event.total) * 100));
        });
        xhr.addEventListener('load', () => {
            let body = null;
            try {
                body = JSON.parse(xhr.responseText);
            } catch {}
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject({ status: xhr.status, detail: body?.detail });
        });
        xhr.addEventListener('error', () => reject({ status: 0 }));
        xhr.addEventListener('abort', () => reject({ status: 0, detail: 'Upload aborted' }));
        const form = new FormData();
        form.append('file', blob, 'site.zip');
        xhr.send(form);
    });
}

function errorMessageFor(error) {
    if (!error || error.status === 0) return 'Could not reach the server. Check your connection and try again.';
    if (error.status === 401) return 'Session expired. Please refresh and sign in again.';
    if (error.detail) return error.detail;
    if (error.status === 403) return 'Not allowed to deploy to that site.';
    if (error.status === 400) return 'Invalid upload.';
    if (error.status === 413) return 'Upload is too large.';
    return 'Deploy failed (HTTP ' + error.status + ').';
}

function totalSize(files) {
    return files.reduce((total, file) => total + (file.size || 0), 0);
}

async function runDeploy(makeBlob, subdomain, makePrivate) {
    try {
        const blob = await makeBlob();
        if (!blob || (blob.size !== undefined && blob.size === 0)) {
            setUploadStatus('error', 'Nothing to upload (all files were filtered out).');
            return false;
        }
        setUploadStatus('progress', progressMarkup('Uploading...', 0));
        const data = await uploadBlob(blob, subdomain, makePrivate);
        const url = data?.url || '';
        const safeUrl = esc(url);
        setUploadStatus('success',
            '<p class="text-xl font-bold">Deploy complete</p>' +
            '<a href="' + safeUrl + '" target="_blank" rel="noopener" class="mt-2 inline-block font-bold text-paper underline decoration-2 underline-offset-4">' + safeUrl + ' (opens in new tab)</a>' +
            '<p class="mt-2">' + window.visibilityBadge(Boolean(data?.private)) + '</p>'
        );
        document.getElementById('file-input').value = '';
        document.getElementById('folder-input').value = '';
        if (typeof window.onDeploySuccess === 'function') window.onDeploySuccess(data);
        return true;
    } catch (error) {
        setUploadStatus('error', esc(errorMessageFor(error)));
        return false;
    }
}

let staged = null;
let isRedeployMode = false;

function clearStaged() {
    staged = null;
    document.getElementById('staged-selection').classList.add('hidden');
    document.getElementById('staged-name').textContent = '';
    document.getElementById('staged-detail').textContent = '';
    document.getElementById('deploy-dialog-submit').disabled = true;
    document.getElementById('file-input').value = '';
    document.getElementById('folder-input').value = '';
}

function renderStaged() {
    document.getElementById('staged-name').textContent = staged.label;
    document.getElementById('staged-detail').textContent = staged.detail || '';
    document.getElementById('staged-selection').classList.remove('hidden');
    document.getElementById('deploy-dialog-submit').disabled = false;
    clearUploadStatus();
    if (!isRedeployMode) {
        setTimeout(() => document.getElementById('subdomain-input').focus(), 0);
    }
}

function stageZip(file) {
    staged = { kind: 'zip', payload: file, label: file.name, detail: formatSize(file.size) };
    renderStaged();
}

function stageFiles(files) {
    const size = totalSize(files);
    if (size > MAX_UPLOAD_BYTES) {
        clearStaged();
        setUploadStatus('error', 'Too large: ' + formatSize(size) + ' exceeds the ' + formatSize(MAX_UPLOAD_BYTES) + ' limit.');
        return;
    }
    if (files.length === 1 && /\.zip$/i.test(files[0].name)) {
        stageZip(files[0]);
        return;
    }
    const paths = files.map(file => file.webkitRelativePath || file.name);
    const firstSegment = paths[0].split('/')[0];
    const sharedRoot = paths.length > 0 && paths.every(path => path.split('/')[0] === firstSegment && path.includes('/'));
    let label;
    if (sharedRoot) label = firstSegment + '/';
    else if (files.length === 1) label = files[0].name;
    else label = files.length + ' files';
    const detail = files.length + ' file' + (files.length === 1 ? '' : 's') + ' · ' + formatSize(size);
    staged = { kind: 'files', payload: files, label, detail };
    renderStaged();
    if (size > WARN_UPLOAD_BYTES) {
        setUploadStatus('progress', 'Large upload (' + formatSize(size) + ') - this may take a while.');
    }
}

function stageEntries(entries) {
    const label = entries.length === 1
        ? entries[0].name + (entries[0].isDirectory ? '/' : '')
        : entries.length + ' items';
    staged = { kind: 'entries', payload: entries, label, detail: 'Folder - file list will be read on deploy.' };
    renderStaged();
}

function stageDroppedItems(items, files) {
    const entries = [];
    for (const item of items) {
        const entry = item.webkitGetAsEntry?.();
        if (entry) entries.push(entry);
    }
    if (entries.some(entry => entry?.isDirectory)) {
        stageEntries(entries);
        return;
    }
    const fileList = [...files];
    if (!fileList.length) {
        setUploadStatus('error', 'Drop a file or folder to deploy.');
        return;
    }
    stageFiles(fileList);
}

async function deployStaged() {
    if (!staged) return;
    const submitButton = document.getElementById('deploy-dialog-submit');
    submitButton.disabled = true;
    const subdomain = document.getElementById('subdomain-input').value.trim();
    const makePrivate = !isRedeployMode && document.getElementById('deploy-private').checked;
    let succeeded = false;
    try {
        if (staged.kind === 'zip') {
            succeeded = await runDeploy(async () => staged.payload, subdomain, makePrivate);
        } else if (staged.kind === 'files') {
            const files = staged.payload;
            const hasRelativePaths = files.some(file => file.webkitRelativePath?.includes('/'));
            succeeded = await runDeploy(async () => {
                const zip = hasRelativePaths ? zipFromRelativeFiles(files) : zipFromLooseFiles(files);
                return generateZipBlob(zip, 'Zipping');
            }, subdomain, makePrivate);
        } else if (staged.kind === 'entries') {
            succeeded = await runDeploy(async () => {
                setUploadStatus('progress', progressMarkup('Reading folder...', 0));
                return generateZipBlob(await zipFromEntries(staged.payload), 'Zipping');
            }, subdomain, makePrivate);
        }
    } finally {
        submitButton.disabled = succeeded;
    }
}

export function openDeployDialog(redeploySubdomain) {
    const dialog = document.getElementById('deploy-dialog');
    const title = document.getElementById('deploy-dialog-title');
    const subtitle = document.getElementById('deploy-dialog-subtitle');
    const subdomainInput = document.getElementById('subdomain-input');
    const subdomainHint = document.getElementById('subdomain-hint');
    const visibility = document.getElementById('deploy-visibility');

    clearStaged();
    clearUploadStatus();
    isRedeployMode = Boolean(redeploySubdomain);
    if (redeploySubdomain) {
        title.textContent = 'Redeploy ' + redeploySubdomain;
        subtitle.textContent = 'Drop a folder, an HTML file, or a ZIP. The existing site will be replaced.';
        subdomainInput.value = redeploySubdomain;
        subdomainInput.readOnly = true;
        subdomainInput.classList.add('opacity-60', 'cursor-not-allowed');
        subdomainHint.classList.remove('hidden');
        visibility.classList.add('hidden');
    } else {
        title.textContent = 'Deploy a site';
        subtitle.textContent = 'Drop a folder, an HTML file, or a ZIP to deploy.';
        subdomainInput.value = '';
        subdomainInput.readOnly = false;
        subdomainInput.classList.remove('opacity-60', 'cursor-not-allowed');
        subdomainHint.classList.add('hidden');
        document.getElementById('deploy-private').checked = false;
        visibility.classList.remove('hidden');
    }
    window.BuzzDialogs.open(dialog, document.activeElement);
}

export function closeDeployDialog() {
    window.BuzzDialogs.close(document.getElementById('deploy-dialog'));
}

function init() {
    const dialog = document.getElementById('deploy-dialog');
    const zone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const folderInput = document.getElementById('folder-input');
    window.BuzzDialogs.onCancel(dialog, closeDeployDialog);
    document.getElementById('pick-files').addEventListener('click', () => fileInput.click());
    document.getElementById('pick-folder').addEventListener('click', () => folderInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) stageFiles([...fileInput.files]);
    });
    folderInput.addEventListener('change', () => {
        if (folderInput.files.length) stageFiles([...folderInput.files]);
    });
    for (const eventName of ['dragenter', 'dragover']) {
        zone.addEventListener(eventName, event => {
            event.preventDefault();
            event.stopPropagation();
            zone.classList.add('border-solid', 'bg-focus/30');
        });
    }
    for (const eventName of ['dragleave', 'drop']) {
        zone.addEventListener(eventName, event => {
            event.preventDefault();
            event.stopPropagation();
            zone.classList.remove('border-solid', 'bg-focus/30');
        });
    }
    zone.addEventListener('drop', event => {
        const items = event.dataTransfer.items ? [...event.dataTransfer.items] : [];
        stageDroppedItems(items, event.dataTransfer.files);
    });
    document.getElementById('staged-clear').addEventListener('click', () => {
        clearStaged();
        clearUploadStatus();
    });
    document.getElementById('deploy-dialog-submit').addEventListener('click', deployStaged);
    document.getElementById('deploy-dialog-close').addEventListener('click', closeDeployDialog);
    window.addEventListener('dragover', event => event.preventDefault());
    window.addEventListener('drop', event => event.preventDefault());
}

init();
