import { writable } from 'svelte/store';

/** 全局 Toast 队列:右上角堆叠,自动消失。
 * 替代原页面顶部 pageError 红条与"成功无反馈"(设计稿 I4)。 */
export type ToastItem = {
	id: number;
	kind: 'ok' | 'err';
	message: string;
};

export const toasts = writable<ToastItem[]>([]);

let nextId = 1;
const TIMEOUT_MS = 4000;

function push(kind: ToastItem['kind'], message: string) {
	if (!message) return;
	const id = nextId++;
	toasts.update((items) => [...items, { id, kind, message }]);
	setTimeout(() => dismissToast(id), TIMEOUT_MS);
}

export function toastSuccess(message: string) {
	push('ok', message);
}

export function toastError(message: string) {
	push('err', message);
}

export function dismissToast(id: number) {
	toasts.update((items) => items.filter((item) => item.id !== id));
}
