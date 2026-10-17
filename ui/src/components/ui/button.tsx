import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-blue-700',
        destructive: 'bg-destructive text-white hover:bg-red-700',
        outline: 'border border-input bg-background text-gray-700 hover:bg-gray-50 hover:text-gray-900',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-gray-200',
        ghost: 'text-gray-500 hover:bg-gray-100 hover:text-gray-800',
        link: 'text-primary hover:underline underline-offset-4',
      },
      size: {
        default: 'h-8 px-3 text-body',
        lg: 'h-10 px-4 text-body',
        sm: 'h-7 px-2.5 text-meta',
        xs: 'h-6 gap-1 px-2 text-caption [&_svg:not([class*=size-])]:size-3',
        icon: 'size-8',
        'icon-sm': 'size-7',
        'icon-xs': 'size-6 [&_svg:not([class*=size-])]:size-3.5',
        inline: 'h-auto p-0',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : 'button'
  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />
}

export { Button, buttonVariants }
