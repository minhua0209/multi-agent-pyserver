export const TOAST_VISIBLE_MS = 2400
export const TOAST_FADE_MS = 420
export const TOAST_DISMISS_MS = TOAST_VISIBLE_MS + TOAST_FADE_MS

export interface ToastMessage {
  id: number
  text: string
}

export function createToastMessage(text: string, id: number): ToastMessage | null {
  const normalized = text.trim()
  return normalized ? { id, text: normalized } : null
}

export function shouldDismissToast(current: ToastMessage | null, scheduledId: number) {
  return current?.id === scheduledId
}
