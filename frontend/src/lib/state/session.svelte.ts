import { AdminApiClient } from '$lib/api/client';
import type {
	AuthProfile,
	ReadyStatus,
	RegisterResponse,
	LoginResponse
} from '$lib/api/types';
import {
	clearStoredSessionToken,
	loadStoredSessionToken,
	persistSessionToken
} from '$lib/state/admin-token';
import { employeeIdPattern, errorMessage } from '$lib/admin-config';

export interface LoginForm {
	username: string;
	password: string;
}

export interface RegisterForm {
	username: string;
	full_name: string;
	password: string;
}

export interface LoginResult {
	ok: boolean;
	error?: string;
	profile?: AuthProfile;
	plaintextKey?: string;
}

/**
 * Session store: owns sessionToken/connected/loading/pageError/plaintextKey/ready
 * plus the auth actions (login/register/logout/loadProfile/refreshReady).
 *
 * The store holds ONLY session concern. Page-level concerns (inventory, forms,
 * admin sections) stay local to +page.svelte and are driven through the
 * callbacks exposed by login()/register()/logout().
 */
export function createSessionStore() {
	let sessionToken = $state(loadStoredSessionToken());
	let rememberSession = $state(true);
	let connected = $state(false);
	let loading = $state(false);
	let pageError = $state('');
	let plaintextKey = $state('');
	let ready = $state<ReadyStatus | null>(null);

	// Derived API client bound to the current token. Re-created whenever
	// sessionToken changes so downstream fetches carry the fresh header.
	const api = $derived(new AdminApiClient('', sessionToken));

	function setConnected(profile: AuthProfile | null) {
		connected = profile !== null;
	}

	/**
	 * Login with username/password. On success calls onAuthed(profile) so the
	 * page can wire inventory/realtime/own-usage. Mirrors the original
	 * loginAccount logic verbatim.
	 */
	async function login(
		form: LoginForm,
		opts: {
			remember: boolean;
			fromStorage?: boolean;
			onAuthed: (profile: AuthProfile, api: AdminApiClient) => Promise<void>;
		}
	): Promise<LoginResult> {
		if (!form.username.trim() || !form.password) {
			pageError = '请输入用户名和密码。';
			return { ok: false };
		}
		loading = true;
		pageError = '';
		try {
			const response = await new AdminApiClient().post<LoginResponse>('/auth/login', form);
			sessionToken = response.session_token;
			rememberSession = opts.remember;
			connected = true;
			if (!opts.fromStorage) persistSessionToken(sessionToken, rememberSession);
			await opts.onAuthed(response.profile, new AdminApiClient('', sessionToken));
			return { ok: true, profile: response.profile };
		} catch (error) {
			pageError = errorMessage(error);
			return { ok: false, error: pageError };
		} finally {
			loading = false;
		}
	}

	/**
	 * Register a new self-service account. On success exposes the issued
	 * plaintext key and calls onAuthed(profile). Mirrors registerAccount.
	 */
	async function register(
		form: RegisterForm,
		opts: {
			remember: boolean;
			onAuthed: (profile: AuthProfile, api: AdminApiClient) => Promise<void>;
		}
	): Promise<LoginResult> {
		if (!employeeIdPattern.test(form.username.trim())) {
			pageError = '工号必须是 1 个字母加 8 位数字，例如 l00014624。';
			return { ok: false };
		}
		if (!form.full_name.trim() || form.password.length < 8) {
			pageError = '请输入真实姓名，密码至少 8 个字符。';
			return { ok: false };
		}
		loading = true;
		pageError = '';
		try {
			const response = await new AdminApiClient().post<RegisterResponse>('/auth/register', form);
			sessionToken = response.session_token;
			rememberSession = opts.remember;
			plaintextKey = response.gateway_key.plaintext_key;
			connected = true;
			persistSessionToken(sessionToken, rememberSession);
			await opts.onAuthed(response.profile, new AdminApiClient('', sessionToken));
			return { ok: true, profile: response.profile, plaintextKey: plaintextKey };
		} catch (error) {
			pageError = errorMessage(error);
			return { ok: false, error: pageError };
		} finally {
			loading = false;
		}
	}

	/**
	 * Load the current profile using an existing token (from storage or after
	 * login). Mirrors loadProfile. Returns the profile so the page can branch
	 * on admin vs. non-admin.
	 */
	async function loadProfile(
		opts: {
			fromStorage?: boolean;
			remember?: boolean;
			onAuthed: (profile: AuthProfile, api: AdminApiClient) => Promise<void>;
		}
	): Promise<AuthProfile | null> {
		loading = true;
		pageError = '';
		try {
			const profile = await api.get<AuthProfile>('/auth/me');
			connected = true;
			if (!opts.fromStorage) persistSessionToken(sessionToken, opts.remember ?? rememberSession);
			await opts.onAuthed(profile, api);
			return profile;
		} catch (error) {
			pageError = errorMessage(error);
			return null;
		} finally {
			loading = false;
		}
	}

	/** Clear all session state and stored token. Mirrors disconnect(). */
	function logout() {
		sessionToken = '';
		connected = false;
		plaintextKey = '';
		pageError = '';
		// Health status (ready) is independent of auth; leave as-is.
		clearStoredSessionToken();
	}

	/** Health check: GET /health/ready. Mirrors refreshReady. */
	async function refreshReady(): Promise<void> {
		try {
			const response = await fetch('/health/ready');
			ready = (await response.json()) as ReadyStatus;
		} catch {
			ready = null;
		}
	}

	return {
		get sessionToken() {
			return sessionToken;
		},
		get rememberSession() {
			return rememberSession;
		},
		set rememberSession(v: boolean) {
			rememberSession = v;
		},
		get connected() {
			return connected;
		},
		get loading() {
			return loading;
		},
		set loading(v: boolean) {
			loading = v;
		},
		get pageError() {
			return pageError;
		},
		set pageError(v: string) {
			pageError = v;
		},
		get plaintextKey() {
			return plaintextKey;
		},
		set plaintextKey(v: string) {
			plaintextKey = v;
		},
		get ready() {
			return ready;
		},
		get api() {
			return api;
		},
		login,
		register,
		logout,
		loadProfile,
		refreshReady,
		setConnected
	};
}

export type SessionStore = ReturnType<typeof createSessionStore>;
