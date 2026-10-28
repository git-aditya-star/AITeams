import { configureStore, type ThunkAction, type UnknownAction } from '@reduxjs/toolkit'
import { useDispatch, useSelector } from 'react-redux'
import { conversations, dashboard, docPanel, features, inbox, locks, messages, questions, session, team } from './slices'

// Shared, server-fed state of the open project (REST loads + WS frames). URL state (conversation, tab, review)
// stays in the router; form drafts and busy flags stay in their components.
export const store = configureStore({
  reducer: {
    session: session.reducer,
    conversations: conversations.reducer,
    features: features.reducer,
    messages: messages.reducer,
    inbox: inbox.reducer,
    team: team.reducer,
    dashboard: dashboard.reducer,
    questions: questions.reducer,
    locks: locks.reducer,
    docPanel: docPanel.reducer,
  },
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
export type AppThunk<R = void> = ThunkAction<R, RootState, unknown, UnknownAction>

export const useAppDispatch = useDispatch.withTypes<AppDispatch>()
export const useAppSelector = useSelector.withTypes<RootState>()
