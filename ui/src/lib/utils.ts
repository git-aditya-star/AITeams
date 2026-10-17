import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// The type tokens are font sizes; plain twMerge reads unknown `text-*` as colours, so
// cn('text-meta', 'text-gray-500') would drop one of them.
const twMerge = extendTailwindMerge({
  extend: { classGroups: { 'font-size': [{ text: ['title', 'body', 'meta', 'caption', 'code'] }] } },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
