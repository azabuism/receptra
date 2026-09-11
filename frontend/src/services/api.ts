import axios from 'axios';
import type {
  Shop, Customer, Reservation, Review, Promotion, Notification,
  ShopRegisterRequest, CustomerRegisterRequest, ReservationCreateRequest,
  ReviewCreateRequest, PromotionCreateRequest, NotificationCreateRequest,
  ApiResponse
} from '../types';

const api = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Authorization': 'Bearer test_token_123'
  }
});

export const shopAPI = {
  register: (data: ShopRegisterRequest) => api.post<Shop>('/shops/register', data),
  search: (query: string) => api.get<Shop[]>('/shops/search', { params: { q: query } }),
  getDetails: (shopId: string) => api.get<Shop>(`/shops/${shopId}`)
};

export const customerAPI = {
  register: (data: CustomerRegisterRequest) => api.post<Customer>('/customers/register', data),
  getProfile: (customerId: string) => api.get<Customer>(`/customers/${customerId}`),
  search: (query: string) => api.get<Customer[]>('/customers/search', { params: { q: query } }),
  update: (customerId: string, data: Partial<Customer>) => api.patch<Customer>(`/customers/${customerId}`, data)
};

export const reservationAPI = {
  create: (data: Partial<ReservationCreateRequest>) => api.post<Reservation>('/reservations', data),
  getDetails: (id: string) => api.get<Reservation>(`/reservations/${id}`),
  listByShop: (shopId: string) => api.get<Reservation[]>(`/reservations/shop/${shopId}`),
  update: (id: string, data: Partial<Reservation>) => api.patch<Reservation>(`/reservations/${id}`, data),
  delete: (id: string) => api.delete(`/reservations/${id}`)
};

export const reviewAPI = {
  submit: (data: ReviewCreateRequest) => api.post<Review>('/reviews', data),
  listByShop: (shopId: string) => api.get<Review[]>(`/reviews/shop/${shopId}`),
  update: (id: string, data: Partial<Review>) => api.patch<Review>(`/reviews/${id}`, data),
  delete: (id: string) => api.delete(`/reviews/${id}`)
};

export const ratingAPI = {
  submit: (data: { shop_id: string; customer_id: string; rating: number }) => 
    api.post('/ratings', data),
  listByShop: (shopId: string) => api.get(`/ratings/shop/${shopId}`)
};

export const promotionAPI = {
  create: (data: Partial<PromotionCreateRequest>) => api.post<Promotion>('/promotions', data),
  listByShop: (shopId: string) => api.get<Promotion[]>(`/promotions/shop/${shopId}`),
  getDetails: (id: string) => api.get<Promotion>(`/promotions/${id}`),
  getByCode: (code: string) => api.get<Promotion>(`/promotions/code/${code}`),
  update: (id: string, data: Partial<Promotion>) => api.patch<Promotion>(`/promotions/${id}`, data),
  delete: (id: string) => api.delete(`/promotions/${id}`),
  use: (id: string, customerId: string) => api.post(`/promotions/${id}/use`, { customer_id: customerId })
};

export const notificationAPI = {
  create: (data: NotificationCreateRequest) => api.post<Notification>('/notifications', data),
  list: (params: any) => api.get<{ items: Notification[] }>('/notifications', { params }),
  getDetails: (id: string) => api.get<Notification>(`/notifications/${id}`),
  markAsRead: (id: string, data: { is_read: boolean }) => 
    api.patch<Notification>(`/notifications/${id}`, data),
  batchMarkAsRead: (ids: string[], data: { is_read: boolean }) => 
    api.patch('/notifications/batch', { ids, ...data }),
  delete: (id: string) => api.delete(`/notifications/${id}`),
  clearAll: (recipientId: string) => api.delete(`/notifications/clear/${recipientId}`)
};

export default api;
