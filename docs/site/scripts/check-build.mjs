import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = fileURLToPath(new URL('..', import.meta.url));
const dist = join(siteRoot, 'dist');
const site = new URL('https://buzzstatic.dev');
const requiredFiles = ['index.html', 'llms.txt', 'llms-small.txt', 'llms-full.txt'];
const failures = [];

function filesBelow(directory) {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		return entry.isDirectory() ? filesBelow(path) : [path];
	});
}

function outputPath(pathname) {
	const relativePath = pathname.replace(/^\//, '');
	if (relativePath === '404/') return join(dist, '404.html');
	if (!relativePath || pathname.endsWith('/')) return join(dist, relativePath, 'index.html');
	if (extname(relativePath)) return join(dist, relativePath);
	return join(dist, relativePath, 'index.html');
}

for (const required of requiredFiles) {
	if (!existsSync(join(dist, required))) failures.push(`Missing ${required}`);
}

for (const htmlPath of filesBelow(dist).filter((path) => path.endsWith('.html'))) {
	const html = readFileSync(htmlPath, 'utf8');
	const pagePath = `/${relative(dist, htmlPath).replaceAll('\\', '/').replace(/index\.html$/, '')}`;
	const pageUrl = new URL(pagePath, site);

	for (const match of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
		const value = match[1];
		if (value.startsWith('#') || value.startsWith('data:') || value.startsWith('mailto:')) continue;

		const url = new URL(value, pageUrl);
		if (url.origin !== pageUrl.origin) continue;

		const target = outputPath(url.pathname);
		if (!existsSync(target)) {
			failures.push(`${relative(dist, htmlPath)} links to missing ${url.pathname}`);
			continue;
		}

		if (url.hash && target.endsWith('.html')) {
			const id = decodeURIComponent(url.hash.slice(1));
			const targetHtml = readFileSync(target, 'utf8');
			if (!targetHtml.includes(`id="${id}"`)) {
				failures.push(`${relative(dist, htmlPath)} links to missing #${id} in ${url.pathname}`);
			}
		}
	}
}

for (const llmsFile of ['llms.txt', 'llms-small.txt', 'llms-full.txt']) {
	const content = readFileSync(join(dist, llmsFile), 'utf8');
	if (!content.includes('Buzz')) failures.push(`${llmsFile} does not identify Buzz`);
	if (content.includes('https://infomiho.github.io/')) {
		failures.push(`${llmsFile} contains the old GitHub Pages URL`);
	}
}

if (failures.length > 0) {
	console.error(failures.join('\n'));
	process.exitCode = 1;
} else {
	console.log('Verified built pages, internal links, fragments, and LLM files.');
}
