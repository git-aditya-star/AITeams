import { createContext, useContext } from 'react'
import type { Approval, Commit, Feature } from '../api'

// Lets ApprovalCard/CommitCard (rendered deep inside MessageList) reach the project id,
// resolve feature names and push updated rows back into App state.
export interface CardActions {
  pid: string
  features: Feature[]
  onApproval: (a: Approval) => void
  onCommit: (c: Commit) => void
}

export const CardContext = createContext<CardActions | null>(null)
export const useCards = () => useContext(CardContext)

export const featureLabel = (features: Feature[], id: number | null) => {
  const f = id == null ? undefined : features.find((x) => x.id === id)
  return f ? `${f.key} ${f.title}` : null
}
