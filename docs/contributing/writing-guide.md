# Documentation Writing Guide

Buzz documentation helps readers accomplish a task with the least necessary effort. It does not restate the source code. It explains why a capability matters, when to use it, and how to use it safely.

This guide adapts the principles of the [Vue documentation writing guide](https://github.com/vuejs/docs/blob/main/.github/contributing/writing-guide.md) to Buzz's product, terminology, and existing direct style.

## Principles

### A Feature Is Not Finished Until It Is Documented

Document user-visible behavior in the same change that introduces it. If a feature is difficult to explain, examine the product design before adding more prose.

### Respect The Reader's Attention

Readers have limited time and cognitive capacity.

- Introduce one new concept at a time.
- Prefer short sentences and familiar words.
- Keep the main path free of optional detail.
- Put advanced combinations in later guides or reference pages.
- Do not make readers follow several links to complete one basic task.

### Start With The Problem

Explain why or when a reader needs a feature before naming the mechanism. A heading such as "Keep A Site Name Across Deployments" gives more context than "Using CNAME".

### Meet Readers Where They Are

State prerequisites at the start of a page. Do not assume familiarity with Buzz internals, DNS, OAuth, Docker, or GitHub Actions unless the page says so.

### Optimize For Power Versus Effort

Teach the smallest set of concepts that unlocks the most useful result. The getting-started path should produce a live site in less than ten minutes. Less common configuration belongs in focused guides or reference pages.

### Show Working Examples

Prefer a short command and its expected result over a paragraph describing the command. Examples must work in the context described by the page.

### Link Instead Of Duplicating

Give each behavior one authoritative explanation. Summarize it elsewhere only when the reader needs local context, then link to the source page.

### Publish And Improve

Publish useful documentation when it is accurate and clear. Improve it through review and reader feedback rather than waiting for exhaustive coverage.

## Page Types

### Getting Started

Help a new reader understand Buzz, install the CLI, sign in, deploy one site, and open it. Keep the path linear and avoid optional configuration.

### Guides

Help a reader complete one task. Start with the goal and prerequisites, present steps in order, show the expected result, and link to related tasks at the end.

### Self-Hosting Guides

Help an operator make and verify a change. Include prerequisites, persistent data, security consequences, rollback or recovery information, and a verification step when relevant.

### Reference

Describe all supported commands, options, configuration, values, or responses. Optimize for scanning rather than sequential reading. Use consistent entries and concrete examples.

### Troubleshooting

Start with a symptom the reader can recognize. List checks in the safest and most likely order. Explain what each result means before suggesting the next action.

## Voice And Tone

- Address the reader as "you".
- Use active voice and present tense.
- Start instructions with an action verb.
- Be direct, calm, and factual.
- Use contractions when they make a sentence sound natural.
- Explain consequences before destructive actions.
- Prefer specific statements over broad claims.

Do not use:

- "Easy," "just," "simply," "obviously," or similar words that dismiss difficulty.
- Humor, sarcasm, pop-culture references, or emojis.
- Vague appeals to "best practice." Explain the concrete benefit or risk.
- Marketing claims such as "blazing fast" or "effortless."
- "We" when "Buzz" or a direct instruction is clearer.

Existing Buzz phrases such as "Deploy sites with a single command" are appropriate when the page immediately shows the command and its prerequisites.

## Structure

- Use Title Case for page titles and headings.
- Make task headings describe the reader's goal or problem.
- Begin with one or two sentences that define the page's outcome.
- State prerequisites before the first step.
- Use numbered lists for ordered procedures.
- Use bullets for unordered choices or facts.
- Keep paragraphs focused on one idea.
- Put essential caveats in the normal flow.
- Use no more than two callouts on a page. Never place callouts next to each other.

Do not add an "Introduction" heading when the opening paragraph already introduces the page.

## Language And Grammar

- Prefer plain language over jargon.
- Define an abbreviation before using it, unless it is part of a command or public API.
- Use the Oxford comma.
- End a sentence that introduces the next code block with a colon.
- Use the capitalization preferred by a project, such as GitHub, Coolify, npm, FastAPI, and Cloudflare.
- Use `and` in prose instead of `&`.
- Avoid directional language such as "above" and "below" when a section name is clearer.
- Use "for example" instead of the Latin abbreviations "e.g." and "i.e."

## Buzz Terminology

Use these terms consistently.

| Term | Meaning And Usage |
| --- | --- |
| Buzz | The product. Do not write "Buzz Hosting" unless naming a specific package or UI label. |
| Buzz server | A running Buzz installation used by the CLI and dashboard. |
| Server URL | The URL configured by the CLI to reach a Buzz server. |
| Buzz domain | The base hostname configured with `BUZZ_DOMAIN`, such as `buzz.example.com`. Do not call it a subdomain. |
| Site | One deployed set of static files and its Buzz metadata. |
| Site name | The unique name that becomes the first label in a site's hostname, such as `my-site`. Prefer this term when discussing identity. |
| Subdomain | The DNS portion of a site URL. Use it when discussing URLs, DNS, or the `--subdomain` option. |
| Site URL | The public URL for a site, such as `https://my-site.buzz.example.com`. |
| Deployment | An upload that creates or replaces a site. |
| Redeployment | A deployment that replaces an existing site's files. |
| Deployment token | A site-scoped credential used to deploy without an interactive GitHub session. Do not shorten this to "deploy token" or "auth token." |
| Session | The credential created after signing in with GitHub. It is distinct from a deployment token. |
| Dashboard | Buzz's browser interface. |
| `CNAME` file | A local project file containing a Buzz site name. State explicitly that it is not a DNS CNAME record when confusion is possible. |

Use "sign in" and "sign out" in prose. Use the exact command names `buzz login` and `buzz logout` when referring to the CLI.

## Commands And Code

- Put commands, file names, paths, options, environment variables, HTTP paths, and literal values in backticks.
- Use fenced code blocks with a language such as `bash`, `yaml`, `json`, or `text`.
- Do not include a shell prompt such as `$`; readers should be able to copy the command.
- Use long option names in examples because they explain themselves.
- Show the shortest complete command for the task.
- Explain placeholders before or immediately after the example.
- Show expected output only when it confirms success or teaches an important distinction.
- Never put real tokens, credentials, personal domains, or account identifiers in examples.

Use concrete, consistent example values:

```text
Buzz domain: buzz.example.com
Site name: my-site
Site URL: https://my-site.buzz.example.com
Build directory: ./dist
```

Use valid command syntax:

```bash
buzz deploy ./dist --subdomain my-site
```

Avoid combining unrelated lessons in one example. A deployment-token guide can assume the reader already knows how to deploy a site and link to that guide.

## UI Instructions

- Use the exact visible label and make it bold: select **GitHub Actions**.
- Separate a navigation path with `>`: open **Settings > Pages**.
- Describe the goal before a long series of clicks.
- Update screenshots only when the visual location is important. Prefer text for stable, accessible instructions.

## Links And References

- Use descriptive link text. Do not use "click here" or expose a raw URL in prose.
- Link to the authoritative page rather than repeating its steps.
- Prefer repository-relative links for repository documents.
- Prefer stable official documentation when linking outside the repository.
- Check that links work under the GitHub Pages base path, not only in local development.

## Notes And Warnings

Use a callout only when its meaning would be lost in normal prose:

- A note supplies context needed by some readers.
- A caution helps prevent a recoverable problem.
- A danger callout precedes data loss, credential exposure, or another serious consequence.

If a page needs several warnings, restructure the explanation or improve the product workflow instead of stacking callouts.

## Generated Documentation

Do not edit generated CLI, configuration, or HTTP API reference pages. Change the authoritative code or metadata and regenerate them.

Hand-written guides should explain intent, workflows, and tradeoffs. Generated reference should own exhaustive syntax and field lists. Link between them instead of copying generated tables into guides.

## Accessibility

- Give images useful alternative text that describes their purpose.
- Do not rely on color alone to convey meaning.
- Use meaningful link text and a logical heading order.
- Keep tables narrow. Use lists when a table would require horizontal scrolling to understand.
- Include text instructions with any screenshot.

## Visual Style

The documentation site follows the same Achroma visual system as the Buzz dashboard:

- Use near-black ink on white paper.
- Reserve blue for links and information, red for errors, green for success, and yellow for focus.
- Use Arial and system sans-serif fonts, with the existing monospace stack for code.
- Use square corners, strong borders, and visible keyboard focus.
- Keep layouts dense enough to scan without reducing readable line lengths.
- Do not add decorative gradients, shadows, animation, or a separate dark theme.

Preserve Starlight's navigation and search behavior. Theme those controls instead of replacing accessible documentation components with custom widgets.

## Review Checklist

- Does the page start with the reader's problem or goal?
- Is the intended audience clear?
- Are prerequisites explicit?
- Does each section introduce one main concept?
- Do commands and examples match current behavior?
- Does the page use canonical Buzz terminology?
- Is duplicated content replaced with a link where possible?
- Are destructive or security-sensitive consequences clear?
- Are headings, links, code blocks, and images accessible?
- Can a reader verify that the task succeeded?
- Has the author proofread the page and checked its links?
