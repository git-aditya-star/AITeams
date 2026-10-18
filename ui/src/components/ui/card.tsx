import * as React from 'react'

import { cn } from '@/lib/utils'

// Kit card (shadcn `card`, retuned to the type tokens). The Dashboard's cards use CardHeader's `title` (an
// uppercase caption heading) and `headline` (a slot on the right), then CardContent.
function Card({ className, ...props }: React.ComponentProps<'div'>) {
  return <div data-slot="card" className={cn('flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-xs', className)} {...props} />
}

function CardHeader({ title, headline, className, ...props }: Omit<React.ComponentProps<'div'>, 'title'> & { title: React.ReactNode; headline?: React.ReactNode }) {
  return (
    <div data-slot="card-header" className={cn('flex h-5 items-center justify-between gap-3', className)} {...props}>
      <CardTitle>{title}</CardTitle>
      {headline != null && <div data-slot="card-headline" className="text-meta text-gray-600">{headline}</div>}
    </div>
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<'h2'>) {
  return <h2 data-slot="card-title" className={cn('text-caption font-semibold uppercase tracking-wide text-gray-500', className)} {...props} />
}

function CardContent({ className, ...props }: React.ComponentProps<'div'>) {
  return <div data-slot="card-content" className={cn('min-w-0 flex-1', className)} {...props} />
}

export { Card, CardContent, CardHeader, CardTitle }
