// Types for ports.mjs (plain JS so shot.mjs can import it without a build step).
export declare const ROLE_PORTS: Record<'manager' | 'lead' | 'lead-2' | 'product' | 'frontend' | 'backend' | 'qa', number>
export declare const role: () => keyof typeof ROLE_PORTS | null
export declare const rolePort: (fallback?: number) => number
