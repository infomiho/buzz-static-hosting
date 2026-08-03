# Changelog

## [0.4.0](https://github.com/infomiho/buzzstatic/compare/server-v0.3.0...server-v0.4.0) (2026-08-03)


### Features

* pin the Coolify operator deployment to releases ([#27](https://github.com/infomiho/buzzstatic/issues/27)) ([22dad60](https://github.com/infomiho/buzzstatic/commit/22dad602cc043d49d695251d384b29a0cc72d511))

## [0.3.0](https://github.com/infomiho/buzzstatic/compare/server-v0.2.0...server-v0.3.0) (2026-08-03)


### Features

* add a server version endpoint and CLI compatibility check ([#22](https://github.com/infomiho/buzzstatic/issues/22)) ([a9083a4](https://github.com/infomiho/buzzstatic/commit/a9083a41aa147bef9f5a853189fa26ee49ec8365))
* pin the published server image in the standalone compose ([#21](https://github.com/infomiho/buzzstatic/issues/21)) ([a0b49c4](https://github.com/infomiho/buzzstatic/commit/a0b49c46f7a02570995da54103980501db1d7198))

## [0.2.0](https://github.com/infomiho/buzzstatic/compare/server-v0.1.0...server-v0.2.0) (2026-08-03)


### ⚠ BREAKING CHANGES

* replace path-pattern access with a per-site public/private switch and rename --subdomain to --site

### Features

* activate Cloudflare proxy domains ([208160f](https://github.com/infomiho/buzzstatic/commit/208160f470e58120dbbf9fef348a22753f04974a))
* activate direct custom domains ([0daeb6d](https://github.com/infomiho/buzzstatic/commit/0daeb6d6bd1c03129a659706f16297f0bd955e2e))
* add Buzz Access owner protection ([04c37ac](https://github.com/infomiho/buzzstatic/commit/04c37acf831518f1a615957115a2450394f30e21))
* add drag-and-drop site upload to the dashboard ([d817647](https://github.com/infomiho/buzzstatic/commit/d8176471a8e89877fd011c6f42eb8cf4db5220dd))
* add favicon to the app ([b7b370f](https://github.com/infomiho/buzzstatic/commit/b7b370f12a71e8e0088e8baa882d01bbed282dfe))
* add GitHub OAuth authentication ([1aee1c4](https://github.com/infomiho/buzzstatic/commit/1aee1c41b6ba4c454f33e00678d17201002983b6))
* add passkey authentication, account page, and browser device authorization ([c546136](https://github.com/infomiho/buzzstatic/commit/c5461368590fe160314ba805fea485ba2f102a0c))
* add privacy-preserving site analytics ([a57e674](https://github.com/infomiho/buzzstatic/commit/a57e6746efc0506d1577f9ebd9d09081977f5f96))
* add registration toggle and GitHub username allowlist ([d1f9705](https://github.com/infomiho/buzzstatic/commit/d1f9705952ba4f2e9aebdee0a287b5999255e001))
* add site detail page with file tree, manage/view-live buttons, and design refinements ([fdc20ca](https://github.com/infomiho/buzzstatic/commit/fdc20ca365f3762b4172a38aefe1764cfa2eccad))
* add SPA support via 200.html fallback ([291707e](https://github.com/infomiho/buzzstatic/commit/291707e0e24caeb9e9e7dbc1e03230ea0a5080d1))
* add staged custom domain routing ([6be6984](https://github.com/infomiho/buzzstatic/commit/6be698496c8d8bd780e94913f8f57b6e2e08239d))
* add web dashboard with cookie auth, login flow, and XSS-safe rendering ([d6f1a39](https://github.com/infomiho/buzzstatic/commit/d6f1a3926c12c1e43a8445823fb074b5e1cb92ab))
* adopt updated reference design system styles ([0fc1e2e](https://github.com/infomiho/buzzstatic/commit/0fc1e2e334e1769f8a526d76ee23ad7eea9e2b94))
* automate custom domain transitions ([b2bb57a](https://github.com/infomiho/buzzstatic/commit/b2bb57a657c893c5d08b7ab1dcc6700e8f5a74cf))
* collapse custom domain details ([1104b47](https://github.com/infomiho/buzzstatic/commit/1104b47d5645210b37fef59c7e7f384885776709))
* diagnose Cloudflare proxy domains ([1b67e0e](https://github.com/infomiho/buzzstatic/commit/1b67e0e45ae582493802c7d5138464ed7313c17a))
* extract AuthService with GitHubClient protocol for testable auth ([e99e604](https://github.com/infomiho/buzzstatic/commit/e99e604fcd5abba2f506204954d211dea53789e2))
* extract SiteStore and deploy helpers with tests, harden zip handling ([d017ae6](https://github.com/infomiho/buzzstatic/commit/d017ae65bfc7745a93225552f8d357e79f96be37))
* isolate tenant hosts and harden site publication ([7b0a2cc](https://github.com/infomiho/buzzstatic/commit/7b0a2cce331cdcccc6515b755006bc8c6aa4d7d3))
* manage custom domains from the CLI ([6bb3735](https://github.com/infomiho/buzzstatic/commit/6bb37357d6f74662d226cf0514746bf82f99f594))
* migrate from Caddy to Traefik v3 and add Coolify support ([5429fb4](https://github.com/infomiho/buzzstatic/commit/5429fb480d44476f738c70b33cb7f2f2efa14584))
* progressive disclosure on site detail page ([5be6a2d](https://github.com/infomiho/buzzstatic/commit/5be6a2dd95ed100692d7392228762ad70695bf41))
* publish versioned server Docker images ([3f61bbd](https://github.com/infomiho/buzzstatic/commit/3f61bbde312f2688e18aba998e19631fe2d56105))
* redesign dashboard in gov.uk style ([9ced831](https://github.com/infomiho/buzzstatic/commit/9ced831c94a0cd1b9fdaa224cef5e786db14e924))
* redesign dashboard UI with warm amber theme, refined typography, and polished interactions ([93d5fcd](https://github.com/infomiho/buzzstatic/commit/93d5fcd06c176386752ceaccec6c5ba04b1102c3))
* refine account passkey list layout, date formatting, and remove dialog ([37df2a6](https://github.com/infomiho/buzzstatic/commit/37df2a66c9bd3f7c692434980d958e8e836fd4fd))
* replace path-pattern access with a per-site public/private switch and rename --subdomain to --site ([100ccfe](https://github.com/infomiho/buzzstatic/commit/100ccfe1b373571b6437fe6fb6758ed3fefbd43c))
* share private sites with GitHub readers ([7cae90c](https://github.com/infomiho/buzzstatic/commit/7cae90c507ca6a7fa401a5e657e8ce4297552506))
* show google search terms in site analytics ([391645c](https://github.com/infomiho/buzzstatic/commit/391645c515f9adaf00a2d4149c7626673890bd1d))
* show site views in dashboard ([75df7aa](https://github.com/infomiho/buzzstatic/commit/75df7aa13b5830680ebc94d58438e69294a42ba5))
* simplify custom domain workflow ([d448aae](https://github.com/infomiho/buzzstatic/commit/d448aae590560a6894849ca7c93be68b5ef11913))
* support multiple custom domain aliases ([3f5fe0f](https://github.com/infomiho/buzzstatic/commit/3f5fe0fc819463356bc4f8d70e5ee9925f583f50))
* surface a point-directly task when cloudflare is detected but unsupported ([0dbbbdd](https://github.com/infomiho/buzzstatic/commit/0dbbbddd57cd969e1c34dcdf052467a923d17ef1))
* use GitHub OAuth browser login ([13bfb69](https://github.com/infomiho/buzzstatic/commit/13bfb695144303098a681d76b4deee8fc6876dfe))


### Bug Fixes

* accept 65535-entry archives and purge deploy tokens on site delete ([b1ef84e](https://github.com/infomiho/buzzstatic/commit/b1ef84e02006df86649c0e8aaf72ffcff319e3ed))
* add path traversal protection and unify subdomain validation ([5132073](https://github.com/infomiho/buzzstatic/commit/5132073b98f721d8537c0c397513d09960cfc14a))
* align Buzz Access UI with Achroma ([8f74d06](https://github.com/infomiho/buzzstatic/commit/8f74d06cc4856473375eb1fdf350029ed4a62ded))
* align table cell padding with card header in dashboard ([c134cf2](https://github.com/infomiho/buzzstatic/commit/c134cf26b49c71262d2906433c3ad6278d22f39e))
* balance custom domain spacing ([cf66483](https://github.com/infomiho/buzzstatic/commit/cf66483b07dca08709420b0862d730b8497cc254))
* bundle cloudflare ip ranges inside the package so RANGE_PATH resolves ([a6e72bc](https://github.com/infomiho/buzzstatic/commit/a6e72bc5109340aa01086b7cbfd05de5620e20b1))
* clarify access visibility states ([b27c8cd](https://github.com/infomiho/buzzstatic/commit/b27c8cd1f0ad4ce097f6723ab6dd94c611be79dd))
* **deploy:** stage drops behind an explicit Deploy click ([fc059fa](https://github.com/infomiho/buzzstatic/commit/fc059faaa59edb9a170b1dc515a8af1893faff42))
* harden deploy paths and CLI errors ([21d9e67](https://github.com/infomiho/buzzstatic/commit/21d9e675b8397e57e6f78b47d51dd680e4d206f9))
* hide active domain verification URLs ([1f663bc](https://github.com/infomiho/buzzstatic/commit/1f663bc9e8dd792cdbc635c4669bca2783f8a0d2))
* hide withdrawn domain connection state ([73db490](https://github.com/infomiho/buzzstatic/commit/73db490ad43a9b182dc42901a855b068715da217))
* improve analytics chart ([d3290df](https://github.com/infomiho/buzzstatic/commit/d3290dfeded560527aafd2c3ca2bf6498ab9008a))
* improve sparse analytics chart ([99642af](https://github.com/infomiho/buzzstatic/commit/99642afb13349830fdcd27e434099e99b7513e38))
* include landing.html in Docker image ([60f162e](https://github.com/infomiho/buzzstatic/commit/60f162e454458a96d80f05dd9f6069e1fc28f1f1))
* keep breakdown card spacing intact at column edges ([0a53425](https://github.com/infomiho/buzzstatic/commit/0a534253102ea452cda8d387b5fc967335c57253))
* make coolify wildcard cert config durable ([291cdeb](https://github.com/infomiho/buzzstatic/commit/291cdeb571a2d28432e49062ac142f185497985e))
* make tokens table buttons consistent with sites table style ([2f8d523](https://github.com/infomiho/buzzstatic/commit/2f8d523808e7628596eb44a58581d77c87464fb7))
* move Coolify compose to repo root ([e53c701](https://github.com/infomiho/buzzstatic/commit/e53c701aa1ecf3e8a7b8fbb711d6eb839ee6f905))
* pack analytics breakdown cards masonry-style ([b1699c8](https://github.com/infomiho/buzzstatic/commit/b1699c8d75a7ffcd6e78984765f408dfd1d49d30))
* polish custom domain summaries ([899026c](https://github.com/infomiho/buzzstatic/commit/899026c680b8137efd400f9513945f9b61bc4dcc))
* preserve domain transition target after source failure ([cac65bb](https://github.com/infomiho/buzzstatic/commit/cac65bb919a8cc6f8cca884a4883f7a8f2a1e21b))
* replace Manage button with clickable site name link in dashboard ([98a09f0](https://github.com/infomiho/buzzstatic/commit/98a09f0d43c0954057d78bc70683a55636bfef24))
* restore domain disclosure affordance ([3628a74](https://github.com/infomiho/buzzstatic/commit/3628a741f900397da403f8db8acdf59eb76d95f7))
* scan dashboard JavaScript for Tailwind classes ([cc7f981](https://github.com/infomiho/buzzstatic/commit/cc7f981dc75b21af7a789d1f67b34941123d591e))
* simplify domain verification indicator ([b9f1e56](https://github.com/infomiho/buzzstatic/commit/b9f1e56c5886fecceaa8e0d51653a802e55b51bf))
* tolerate unroutable Cloudflare IPv6 edges ([c4d108b](https://github.com/infomiho/buzzstatic/commit/c4d108b2e48f302ca6d666772c27172a88732c1e))
* unify dashboard disclosure signals ([df239e1](https://github.com/infomiho/buzzstatic/commit/df239e1aa26a947c0d4b399d30699ecd813bcb4d))
* use consistent text-primary color for site name links ([b37941d](https://github.com/infomiho/buzzstatic/commit/b37941d1e04dfa8d96be66ae04a5815c1733e3bd))
* use explicit volume names to preserve data across deploys ([2d87ffc](https://github.com/infomiho/buzzstatic/commit/2d87ffcc03bef85b7ea5934f6bafcaed5f6c499e))
* use Traefik v3.6 for Docker 29 API compatibility ([9f824c2](https://github.com/infomiho/buzzstatic/commit/9f824c2e19b82dd1c16d4667d237cbf8a551a125))


### Performance Improvements

* skip the database for public sites and run access checks off the event loop ([62cd65d](https://github.com/infomiho/buzzstatic/commit/62cd65d0d00d7c627a86424a2b528c2e91c46776))


### Documentation

* add public documentation site ([523fbc1](https://github.com/infomiho/buzzstatic/commit/523fbc16fbddd013c6dd18e03102abcd937af7f0))
* serve documentation from buzzstatic.dev ([b84467d](https://github.com/infomiho/buzzstatic/commit/b84467d14e3145ba1e65330260be90490235c07c))
* update Coolify instructions with raw compose and SSL details ([118b393](https://github.com/infomiho/buzzstatic/commit/118b39309f398d28457b9df8e233061f0a520c79))
