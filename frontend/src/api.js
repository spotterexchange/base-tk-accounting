import axios from 'axios';
import { getToken, clearAuth } from './auth';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001',
});

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearAuth();
      window.location.reload();
    }
    return Promise.reject(error);
  }
);

export const login = (email, password) => api.post('/auth/login', { email, password });
export const getMe = () => api.get('/auth/me');

export const stageFiles = (formData) => api.post('/imports/stage', formData);
export const commitFiles = (formData) => api.post('/imports/commit', formData);

export const getPayouts = (params) => api.get('/payouts/', { params });
export const getPayoutFilterOptions = () => api.get('/payouts/filter-options');
export const getPayoutFiles = (params) => api.get('/payouts/files', { params });

export const getUnsyncedCount = () => api.get('/sync/unsynced-count');
export const startSync = (importFileIds) =>
  api.post('/sync/sales-from-api', importFileIds ? { import_file_ids: importFileIds } : {});
export const getSyncStatus = () => api.get('/sync/status');
export const getLastSyncRun = () => api.get('/sync/last-run');
export const startInventoryRefresh = () => api.post('/sync/inventory-refresh');
export const getInventoryStatus = () => api.get('/sync/inventory-status');
export const getInventoryLastRefresh = () => api.get('/sync/inventory-last-refresh');
export const getPendingPushCount = () => api.get('/sync/pending-push-count');
export const startPushPayments = (importFileIds) =>
  api.post('/sync/push-payments', importFileIds ? { import_file_ids: importFileIds } : {});
export const getPushStatus = () => api.get('/sync/push-status');
export const manualMatchPayout = (payoutId, orderId) =>
  api.post('/sync/manual-match', { payout_id: payoutId, order_id: orderId });
export const getFilesMissingPaymentDate = () => api.get('/sync/files-missing-payment-date');
export const setPaymentDates = (importFileIds, paymentDate) =>
  api.put('/sync/payout-files/payment-dates', { import_file_ids: importFileIds, payment_date: paymentDate });

export const getPurchasers = () => api.get('/purchasers/');
export const updatePurchaser = (id, body) => api.put(`/purchasers/${id}`, body);
export const getOverrides = (id) => api.get(`/purchasers/${id}/overrides`);
export const createOverride = (id, body) => api.post(`/purchasers/${id}/overrides`, body);
export const deleteOverride = (id, overrideId) => api.delete(`/purchasers/${id}/overrides/${overrideId}`);

export const previewPayouts = (dateFrom, dateTo) =>
  api.get('/reports/preview', { params: { date_from: dateFrom, date_to: dateTo } });
export const generatePayouts = (dateFrom, dateTo, purchaserIds) =>
  api.post('/reports/generate', { date_from: dateFrom, date_to: dateTo, purchaser_ids: purchaserIds || null });
export const getPurchaserDetail = (purchaserId, dateFrom, dateTo) =>
  api.get('/reports/purchaser-detail', { params: { purchaser_id: purchaserId, date_from: dateFrom, date_to: dateTo } });
export const getPurchaserPayouts = (includeRolledBack) =>
  api.get('/reports/purchaser-payouts', { params: { include_rolled_back: !!includeRolledBack } });
export const downloadPurchaserPayout = (payoutId) =>
  api.get(`/reports/purchaser-payouts/${payoutId}/download`, { responseType: 'blob' });
export const commitPurchaserPayout = (payoutId) =>
  api.post(`/reports/purchaser-payouts/${payoutId}/commit`);
export const rollbackPurchaserPayout = (payoutId) =>
  api.post(`/reports/purchaser-payouts/${payoutId}/rollback`);

export const dismissMatch = (payoutId, dismissedBy, note) =>
  api.put(`/payouts/${payoutId}/dismiss-match`, { dismissed_by: dismissedBy, note });
export const getBatchStatus = (importFileIds) =>
  api.get('/imports/batch-status', { params: { import_file_ids: importFileIds.join(',') } });
export const getSplitSales = () => api.get('/sales/splits');
export const updateSplits = (saleId, splits) => api.put(`/sales/${saleId}/splits`, { splits });

export const getAdjustments = (params) => api.get('/adjustments', { params });
export const getNeedsOffset = () => api.get('/adjustments/needs-offset');

// CrowdVold Sales Import
export const cvUpload = (formData) => api.post('/cv-import/upload', formData);
export const cvEvents = () => api.get('/cv-import/events');
export const cvEventSearch = (q) => api.get('/cv-import/event-search', { params: { q } });
export const cvSaveMapping = (body) => api.put('/cv-import/events/mapping', body);
export const cvExcludeSale = (orderNumber, excluded, note) =>
  api.put(`/cv-import/sales/${orderNumber}/exclude`, { excluded, note });
export const cvPreview = () => api.get('/cv-import/preview');
export const cvCreate = (orderNumbers) => api.post('/cv-import/create', { order_numbers: orderNumbers });
export const cvStatus = () => api.get('/cv-import/status');
export const ignoreNeedsOffset = (saleId, note) => api.post(`/adjustments/needs-offset/${saleId}/ignore`, { note });
export const unignoreNeedsOffset = (saleId) => api.delete(`/adjustments/needs-offset/${saleId}/ignore`);
export const addManualOffset = (saleId, payload) => api.post(`/adjustments/needs-offset/${saleId}/offset`, payload);
export const getAdjustmentFilterOptions = () => api.get('/adjustments/filter-options');
export const updateAdjustmentNotes = (payoutId, notes) =>
  api.put(`/adjustments/${payoutId}/notes`, { notes });
export const updateAdjustmentTreatment = (payoutId, treatment) =>
  api.put(`/adjustments/${payoutId}/commission-treatment`, { treatment });
export const updateAdjustmentReason = (payoutId, reason) =>
  api.put(`/adjustments/${payoutId}/reason`, { reason });

export const getUsers = () => api.get('/users');
export const createUser = (body) => api.post('/users', body);
export const updateUser = (userId, body) => api.put(`/users/${userId}`, body);
export const resetUserPassword = (userId, password) => api.post(`/users/${userId}/reset-password`, { password });

export const getExpenses = (purchaserId) => api.get('/expenses', { params: { purchaser_id: purchaserId } });
export const createExpense = (body) => api.post('/expenses', body);
export const updateExpense = (expenseId, body) => api.put(`/expenses/${expenseId}`, body);
export const deleteExpense = (expenseId) => api.delete(`/expenses/${expenseId}`);

export const getDuplicateReviews = (params) => api.get('/duplicate-reviews', { params });
export const markDuplicateReviewed = (id, reviewedBy, reviewNotes) =>
  api.put(`/duplicate-reviews/${id}/review`, { reviewed_by: reviewedBy, review_notes: reviewNotes });
