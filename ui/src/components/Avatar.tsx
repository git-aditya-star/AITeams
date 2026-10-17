import { avatarClass, avatarInitial, roleName } from '../roles'
import { Tip } from './ui/tooltip'

// `tip={false}` where a surrounding element already explains the avatar (receipt groups).
export default function Avatar({ author, size = 28, className = '', tip = true }: { author: string; size?: number; className?: string; tip?: boolean }) {
  const circle = (
    <span
      className={`inline-flex items-center justify-center rounded-full font-semibold shrink-0 ${avatarClass(author)} ${className}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.45) }}
    >
      {avatarInitial(author)}
    </span>
  )
  return tip ? <Tip content={roleName(author)}>{circle}</Tip> : circle
}
