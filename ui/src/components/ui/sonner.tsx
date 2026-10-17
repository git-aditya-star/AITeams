import { Toaster as Sonner, type ToasterProps } from 'sonner'

// Light only: no next-themes. App toasts render their own look through toast.custom (Toasts.tsx).
function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      style={{ '--normal-bg': 'var(--popover)', '--normal-text': 'var(--popover-foreground)', '--normal-border': 'var(--border)' } as React.CSSProperties}
      {...props}
    />
  )
}

export { Toaster }
