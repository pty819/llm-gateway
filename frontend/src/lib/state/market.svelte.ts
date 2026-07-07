import type { Paginated } from '$lib/api/types';

/**
 * Shared state machine + actions for the Skill / MCP market sections.
 *
 * `SkillMarketSection.svelte` and `McpMarketSection.svelte` previously carried
 * near-identical copies of: the `mine`/`browse` tab toggle, the browse state
 * machine (items/total/page/size/sort/q/loading/error/loaded), the
 * load/switch/search/sort/page functions, and the like (owner_key + likedSet +
 * likeBusy + toggleLike) logic. Only the artifact-specific UI differs (upload
 * vs publish dialog, download vs expandable detail rows), so that markup stays
 * in each section while this controller owns the shared behaviour.
 *
 * The artifact type `T` must carry the fields the like/browse logic reads.
 * The caller supplies thin client adapters so the controller stays decoupled
 * from `AdminApiClient`'s concrete method names.
 */

export interface MarketItem {
	id: string;
	slug: string;
	owner_subject_id: string;
	owner_name: string | null;
	like_count: number;
}

export interface LikeResult {
	like_count: number;
	liked_by_me?: boolean;
}

export interface BrowseParams {
	q?: string;
	page: number;
	size: number;
	sort: string;
}

export interface MarketControllerConfig<T extends MarketItem> {
	/** Fetch the caller's own published artifacts (the "mine" list). */
	listMine: () => Promise<Paginated<T>>;
	/** Fetch a page of the browse/market listing. */
	browse: (params: BrowseParams) => Promise<Paginated<T>>;
	/** Like an artifact by owner + slug. */
	like: (owner: string, slug: string) => Promise<LikeResult>;
	/** Remove a like by owner + slug. */
	unlike: (owner: string, slug: string) => Promise<LikeResult>;
}

export type MarketController<T extends MarketItem> = ReturnType<typeof createMarketController<T>>;

export function createMarketController<T extends MarketItem>(config: MarketControllerConfig<T>) {
	let tab = $state<'mine' | 'browse'>('mine');

	// —— 我的 ——
	let items = $state<T[]>([]);
	let loading = $state(false);
	let error = $state('');

	async function loadMine() {
		loading = true;
		error = '';
		try {
			const page = await config.listMine();
			items = page.items;
		} catch (err) {
			error = err instanceof Error ? err.message : '加载列表失败。';
		} finally {
			loading = false;
		}
	}

	// —— 市场浏览 ——
	let browseItems = $state<T[]>([]);
	let browseTotal = $state(0);
	let browsePage = $state(1);
	let browseSize = $state(10);
	let browseSort = $state('downloads');
	let browseQ = $state('');
	let browseLoading = $state(false);
	let browseError = $state('');
	let browseLoaded = $state(false);

	async function loadBrowse() {
		browseLoading = true;
		browseError = '';
		try {
			const page = await config.browse({
				q: browseQ.trim() || undefined,
				page: browsePage,
				size: browseSize,
				sort: browseSort
			});
			browseItems = page.items;
			browseTotal = page.total ?? page.items.length;
		} catch (err) {
			browseError = err instanceof Error ? err.message : '加载市场列表失败。';
			browseItems = [];
			browseTotal = 0;
		} finally {
			browseLoading = false;
			browseLoaded = true;
		}
	}

	async function switchTab(next: 'mine' | 'browse') {
		if (tab === next) return;
		tab = next;
		if (next === 'browse' && !browseLoaded) {
			await loadBrowse();
		}
	}

	function onSearch() {
		browsePage = 1;
		void loadBrowse();
	}

	function onSortChange() {
		browsePage = 1;
		void loadBrowse();
	}

	function onPage(p: number) {
		browsePage = p;
		void loadBrowse();
	}

	// —— 点赞 ——
	let likedSet = $state<Set<string>>(new Set());
	let likeBusy = $state<string | null>(null);

	function ownerOf(item: T): string {
		return item.owner_name ?? item.owner_subject_id;
	}

	function likeKey(item: T): string {
		return `${ownerOf(item)}/${item.slug}`;
	}

	function isLiked(item: T): boolean {
		return likedSet.has(likeKey(item));
	}

	/** Reconcile the liked set after a detail fetch reports `liked_by_me`. */
	function syncLiked(item: T, likedByMe: boolean) {
		const key = likeKey(item);
		const next = new Set(likedSet);
		if (likedByMe) next.add(key);
		else next.delete(key);
		likedSet = next;
	}

	async function toggleLike(item: T) {
		const key = likeKey(item);
		likeBusy = key;
		try {
			if (isLiked(item)) {
				const res = await config.unlike(ownerOf(item), item.slug);
				item.like_count = res.like_count;
				const next = new Set(likedSet);
				next.delete(key);
				likedSet = next;
			} else {
				const res = await config.like(ownerOf(item), item.slug);
				item.like_count = res.like_count;
				const next = new Set(likedSet);
				next.add(key);
				likedSet = next;
			}
		} catch (err) {
			browseError = err instanceof Error ? err.message : '操作失败。';
		} finally {
			likeBusy = null;
		}
	}

	return {
		// tab
		get tab() {
			return tab;
		},
		switchTab,
		// mine
		get items() {
			return items;
		},
		get loading() {
			return loading;
		},
		get error() {
			return error;
		},
		loadMine,
		// browse
		get browseItems() {
			return browseItems;
		},
		get browseTotal() {
			return browseTotal;
		},
		get browsePage() {
			return browsePage;
		},
		set browsePage(v: number) {
			browsePage = v;
		},
		get browseSize() {
			return browseSize;
		},
		get browseSort() {
			return browseSort;
		},
		set browseSort(v: string) {
			browseSort = v;
		},
		get browseQ() {
			return browseQ;
		},
		set browseQ(v: string) {
			browseQ = v;
		},
		get browseLoading() {
			return browseLoading;
		},
		get browseError() {
			return browseError;
		},
		set browseError(v: string) {
			browseError = v;
		},
		get browseLoaded() {
			return browseLoaded;
		},
		loadBrowse,
		onSearch,
		onSortChange,
		onPage,
		// like
		get likeBusy() {
			return likeBusy;
		},
		ownerOf,
		likeKey,
		isLiked,
		syncLiked,
		toggleLike
	};
}
