const STORAGE_KEY = 'reachpro_auth';

export function getAuth() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

export function setAuth(auth) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
}

export function clearAuth() {
  localStorage.removeItem(STORAGE_KEY);
}

export function getToken() {
  return getAuth()?.access_token || null;
}

export function getUserName() {
  return getAuth()?.name || 'Unknown';
}

export function getRole() {
  return getAuth()?.role || 'user';
}
