function formatDate(iso) {
    return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const days = Math.floor(diff / 86400000);
    if (days < 1) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 30) return days + 'd ago';
    return formatDate(iso);
}
