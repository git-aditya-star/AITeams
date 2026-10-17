// F-11: each role gets its own QA sandbox port and Playwright output, so two roles' `npm run e2e` (or `shot`) can run
// at the same time without sharing a sandbox or wiping each other's test-results. AITEAMS_ROLE is set in every role
// turn's environment; the Master by hand has none. 8797 and 8799 stay free for QA's live sandboxes.
export const ROLE_PORTS = { manager: 8791, lead: 8792, product: 8793, frontend: 8794, backend: 8795, qa: 8796 }

export const role = () => (process.env.AITEAMS_ROLE in ROLE_PORTS ? process.env.AITEAMS_ROLE : null)

// AITEAMS_QA_PORT wins, then the role's port, then `fallback` (8790 for e2e, 8798 for shot).
export const rolePort = (fallback = 8790) => Number(process.env.AITEAMS_QA_PORT) || ROLE_PORTS[role()] || fallback
