import { createAction } from '@reduxjs/toolkit'
import type { Activity, Approval, Commit, Conversation, Feature, Lock, Message, Question, RateLimitInfo, ReceiptState, Standup, TeamRole, Turn } from '../api'

// Domain events, dispatched both for WS frames and for API responses; every slice that cares handles them.
export const projectOpened = createAction<string>('project/opened')
export const messageReceived = createAction<Message>('message/received')
export const receiptReceived = createAction<{ message_id: number; role: string; state: ReceiptState }>('receipt/received')
export const approvalUpdated = createAction<Approval>('approval/updated')
export const commitUpdated = createAction<Commit>('commit/updated')
export const featureUpserted = createAction<Feature>('feature/upserted')
export const questionUpdated = createAction<Question>('question/updated')
export const turnStateReceived = createAction<Turn>('turn/stateReceived')
export const teamReceived = createAction<TeamRole[]>('team/received')
export const activityReceived = createAction<Activity>('activity/received')
export const standupUpdated = createAction<Standup>('standup/updated')
export const conversationUpserted = createAction<Conversation>('conversation/upserted')
export const locksReceived = createAction<Lock[]>('locks/received')
export const inboxCountReceived = createAction<number>('inbox/countReceived')
export const rateLimitReceived = createAction<RateLimitInfo | null>('rateLimit/received')
export const pausedReceived = createAction<boolean>('paused/received')
