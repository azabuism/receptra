import axios from 'axios';
const api = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Authorization': 'Bearer test_token_123'
    }
});
export const shopAPI = {
    register: (data) => api.post('/shops/register', data),
    search: (query) => api.get('/shops/search', { params: { q: query } }),
    getDetails: (shopId) => api.get(`/shops/${shopId}`)
};
export const customerAPI = {
    register: (data) => api.post('/customers/register', data),
    getProfile: (customerId) => api.get(`/customers/${customerId}`),
    search: (query) => api.get('/customers/search', { params: { q: query } }),
    update: (customerId, data) => api.patch(`/customers/${customerId}`, data)
};
export const reservationAPI = {
    create: (data) => api.post('/reservations', data),
    getDetails: (id) => api.get(`/reservations/${id}`),
    listByShop: (shopId) => api.get(`/reservations/shop/${shopId}`),
    update: (id, data) => api.patch(`/reservations/${id}`, data),
    delete: (id) => api.delete(`/reservations/${id}`)
};
export const reviewAPI = {
    submit: (data) => api.post('/reviews', data),
    listByShop: (shopId) => api.get(`/reviews/shop/${shopId}`),
    update: (id, data) => api.patch(`/reviews/${id}`, data),
    delete: (id) => api.delete(`/reviews/${id}`)
};
export const ratingAPI = {
    submit: (data) => api.post('/ratings', data),
    listByShop: (shopId) => api.get(`/ratings/shop/${shopId}`)
};
export const promotionAPI = {
    create: (data) => api.post('/promotions', data),
    listByShop: (shopId) => api.get(`/promotions/shop/${shopId}`),
    getDetails: (id) => api.get(`/promotions/${id}`),
    getByCode: (code) => api.get(`/promotions/code/${code}`),
    update: (id, data) => api.patch(`/promotions/${id}`, data),
    delete: (id) => api.delete(`/promotions/${id}`),
    use: (id, customerId) => api.post(`/promotions/${id}/use`, { customer_id: customerId })
};
export const notificationAPI = {
    create: (data) => api.post('/notifications', data),
    list: (params) => api.get('/notifications', { params }),
    getDetails: (id) => api.get(`/notifications/${id}`),
    markAsRead: (id, data) => api.patch(`/notifications/${id}`, data),
    batchMarkAsRead: (ids, data) => api.patch('/notifications/batch', { ids, ...data }),
    delete: (id) => api.delete(`/notifications/${id}`),
    clearAll: (recipientId) => api.delete(`/notifications/clear/${recipientId}`)
};
export default api;
