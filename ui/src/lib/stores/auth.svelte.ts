import { getCurrentUser, logout as apiLogout } from '$lib/api/auth';
import type { User, UserRole } from '$lib/api/auth';

let user = $state<User | null>(null);
let loading = $state(true);

const isAuthenticated = $derived(user !== null);
const role = $derived<UserRole | null>(user?.role ?? null);
const isAdmin = $derived(user?.role === 'admin');

async function checkAuth(): Promise<void> {
	loading = true;
	try {
		user = await getCurrentUser();
	} catch {
		user = null;
	} finally {
		loading = false;
	}
}

async function logout(): Promise<void> {
	await apiLogout();
	user = null;
}

function setUser(newUser: User): void {
	user = newUser;
	loading = false;
}

export const auth = {
	get user() {
		return user;
	},
	get loading() {
		return loading;
	},
	get isAuthenticated() {
		return isAuthenticated;
	},
	get role() {
		return role;
	},
	get isAdmin() {
		return isAdmin;
	},

	checkAuth,
	logout,
	setUser,
};
