// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLlmsTxt from 'starlight-llms-txt';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';

const site = 'https://buzzstatic.dev';
const ogImage = new URL('/og-image.png', site).href;
const ogImageAlt = 'Buzz deploys a static build directory from the CLI to a stable site URL.';

// https://astro.build/config
export default defineConfig({
	site,
	integrations: [
		starlight({
			title: 'Buzz',
			description: 'Deploy and operate self-hosted static sites with Buzz.',
			favicon: '/favicon.svg',
			head: [
				{ tag: 'meta', attrs: { property: 'og:image', content: ogImage } },
				{ tag: 'meta', attrs: { property: 'og:image:type', content: 'image/png' } },
				{ tag: 'meta', attrs: { property: 'og:image:width', content: '2400' } },
				{ tag: 'meta', attrs: { property: 'og:image:height', content: '1260' } },
				{ tag: 'meta', attrs: { property: 'og:image:alt', content: ogImageAlt } },
				{ tag: 'meta', attrs: { name: 'twitter:image', content: ogImage } },
				{ tag: 'meta', attrs: { name: 'twitter:image:alt', content: ogImageAlt } },
			],
			social: [
				{
					icon: 'github',
					label: 'Buzz on GitHub',
					href: 'https://github.com/infomiho/buzzstatic',
				},
			],
			editLink: {
				baseUrl: 'https://github.com/infomiho/buzzstatic/edit/main/docs/site/',
			},
			customCss: ['./src/styles/achroma.css'],
			components: {
				ThemeProvider: './src/components/ThemeProvider.astro',
				ThemeSelect: './src/components/ThemeSelect.astro',
				SocialIcons: './src/components/SocialIcons.astro',
			},
			plugins: [
				starlightOpenAPI([
					{
						base: 'reference/http-api',
						schema: './public/openapi.json',
						sidebar: { label: 'HTTP API', collapsed: true },
					},
				]),
				starlightLlmsTxt({
					details:
						'Use Getting Started and Guides for CLI workflows. Use Self-Hosting for server operations. CLI, configuration, and HTTP reference pages are generated from the implementation.',
					promote: [
						'index',
						'getting-started/deploy-your-first-site',
						'getting-started/**',
						'guides/deploy-sites',
						'guides/choose-a-site-name',
						'guides/automate-deployments',
						'guides/**',
						'self-hosting/overview',
					],
					demote: ['contributing/**'],
					exclude: ['404', 'contributing/**', 'reference/http-api/**'],
					customSets: [
						{
							label: 'CLI Workflows',
							description: 'Install the Buzz CLI, deploy sites, and automate deployments.',
							paths: ['getting-started/**', 'guides/**', 'reference/cli'],
						},
						{
							label: 'Server Operations',
							description: 'Install, configure, secure, and troubleshoot a Buzz server.',
							paths: [
								'self-hosting/**',
								'troubleshooting/self-hosting',
								'reference/configuration',
								'reference/hosting-behavior',
							],
						},
					],
				}),
			],
			sidebar: [
				{
					label: 'Getting Started',
					collapsed: true,
					items: [{ autogenerate: { directory: 'getting-started' } }],
				},
				{
					label: 'Guides',
					collapsed: true,
					items: [{ autogenerate: { directory: 'guides' } }],
				},
				{
					label: 'Self-Hosting',
					collapsed: true,
					items: [{ autogenerate: { directory: 'self-hosting' } }],
				},
				{
					label: 'Troubleshooting',
					collapsed: true,
					items: [{ autogenerate: { directory: 'troubleshooting' } }],
				},
				{
					label: 'Reference',
					collapsed: true,
					items: [
						{ label: 'CLI', slug: 'reference/cli' },
						{ label: 'Configuration', slug: 'reference/configuration' },
						{ label: 'Hosting Behavior', slug: 'reference/hosting-behavior' },
						...openAPISidebarGroups,
					],
				},
				{
					label: 'Contributing',
					collapsed: true,
					items: [{ autogenerate: { directory: 'contributing' } }],
				},
			],
		}),
	],
});
