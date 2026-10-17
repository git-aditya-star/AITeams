import { createContext, useContext } from 'react'

// Opens the full-width commit review (a URL change, so it comes from App); everything else cards need is in the store.
export interface CardActions {
  onReview: (commitId: number) => void
}

export const CardContext = createContext<CardActions | null>(null)
export const useCards = () => useContext(CardContext)
