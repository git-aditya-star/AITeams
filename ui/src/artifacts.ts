// F-29 Part B: small helpers shared by the artifact link card and the Docs pane. Free of React.

// The local source is stored as a path under the feature's docs folder, or just its name: the docs list is keyed by the name.
export const sourceName = (path: string) => path.split('/').pop() ?? path

// Only an https link is ever put in an href (the daemon takes the URL from the Artifact tool's result, this is a second guard).
export const claudeHref = (url: string | null | undefined) => (url && /^https:\/\//i.test(url) ? url : null)
