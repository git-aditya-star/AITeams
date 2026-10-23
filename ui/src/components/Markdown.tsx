import { Children, isValidElement } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ROLES } from '../roles'
import Mermaid from './Mermaid'

// Longest name first and no `-`/word char after it, like the daemon's MENTION_RE: `@lead-2` is Lead 2 whole, never
// `@lead` followed by "-2".
const NAMES = [...ROLES, 'lead-1', 'master', 'all'].sort((a, b) => b.length - a.length)
const MENTION_RE = new RegExp(`(^|[^\\w\\[])@(${NAMES.join('|')})(?![\\w-])`, 'g')
// Split out code fences/spans so mentions inside code are left untouched.
const CODE_SPLIT = /(```[\s\S]*?```|`[^`\n]*`)/g

function linkifyMentions(text: string): string {
  return text
    .split(CODE_SPLIT)
    .map((seg, i) => (i % 2 === 1 ? seg : seg.replace(MENTION_RE, (_m, pre, role) => `${pre}[@${role}](#mention-${role})`)))
    .join('')
}

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md wrap-break-word">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // F-29: a ```mermaid fence is a diagram, not a code block (and a <div> can't sit inside <pre>).
          pre: ({ node: _node, children, ...rest }) => {
            const child = Children.toArray(children)[0]
            if (isValidElement<{ className?: string; children?: React.ReactNode }>(child) && /\blanguage-mermaid\b/.test(child.props.className || ''))
              return <Mermaid code={String(child.props.children ?? '').replace(/\n$/, '')} />
            return <pre {...rest}>{children}</pre>
          },
          a: ({ href, children, ...rest }) =>
            href?.startsWith('#mention-') ? (
              <span className="mention">{children}</span>
            ) : (
              <a href={href} target="_blank" rel="noreferrer" {...rest}>
                {children}
              </a>
            ),
        }}
      >
        {linkifyMentions(text)}
      </ReactMarkdown>
    </div>
  )
}
