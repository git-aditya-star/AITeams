import { avatarClass, avatarInitial } from '../roles'

export default function Avatar({ author, size = 28, className = '' }: { author: string; size?: number; className?: string }) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded-full font-semibold shrink-0 ${avatarClass(author)} ${className}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.45) }}
      title={author}
    >
      {avatarInitial(author)}
    </span>
  )
}
