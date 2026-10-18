// F-11: each role gets its own QA sandbox port and Playwright output, so two roles' `npm run e2e` (or `shot`) can run
// at the same time without sharing a sandbox or wiping each other's test-results. AITEAMS_ROLE is set in every role
// turn's environment; the Master by hand has none. 8797 and 8799 stay free for QA's live sandboxes.
// F-22: Lead 2 takes 8798, so `shot` without a role now falls back to 8790 like e2e (one tool call at a time, never both).
export const ROLE_PORTS = { manager: 8791, lead: 8792, 'lead-2': 8798, product: 8793, frontend: 8794, backend: 8795, qa: 8796 }

export const role = () => (process.env.AITEAMS_ROLE in ROLE_PORTS ? process.env.AITEAMS_ROLE : null)

// AITEAMS_QA_PORT wins, then the role's port, then `fallback` (8790 for both e2e and shot).
export const rolePort = (fallback = 8790) => Number(process.env.AITEAMS_QA_PORT) || ROLE_PORTS[role()] || fallback
