// ========== SHOPS ==========
export interface Shop {
  id: string;
  tenant_id: string;
  name: string;
  description?: string;
  address: string;
  phone: string;
  email: string;
  latitude: number;
  longitude: number;
  rating: number;
  review_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ShopRegisterRequest {
  name: string;
  description?: string;
  address: string;
  phone: string;
  email: string;
  latitude: number;
  longitude: number;
}

// ========== CUSTOMERS ==========
export interface Customer {
  id: string;
  tenant_id: string;
  email: string;
  name: string;
  phone?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CustomerRegisterRequest {
  email: string;
  name: string;
  phone?: string;
}

// ========== RESERVATIONS ==========
export interface Reservation {
  id: string;
  tenant_id: string;
  shop_id: string;
  customer_id: string;
  reservation_date: string;
  reservation_time: string;
  guest_count: number;
  special_requests?: string;
  status: 'confirmed' | 'cancelled' | 'completed';
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReservationCreateRequest {
  shop_id: string;
  customer_id: string;
  reservation_date: string;
  reservation_time: string;
  guest_count: number;
  special_requests?: string;
}

// ========== REVIEWS ==========
export interface Review {
  id: string;
  tenant_id: string;
  shop_id: string;
  customer_id: string;
  reservation_id: string;
  rating: number;
  title: string;
  content: string;
  helpful_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReviewCreateRequest {
  shop_id: string;
  customer_id: string;
  reservation_id: string;
  rating: number;
  title: string;
  content: string;
}

// ========== RATINGS ==========
export interface Rating {
  id: string;
  tenant_id: string;
  shop_id: string;
  customer_id: string;
  service: number;
  food: number;
  atmosphere: number;
  value: number;
  cleanliness: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RatingCreateRequest {
  shop_id: string;
  customer_id: string;
  service: number;
  food: number;
  atmosphere: number;
  value: number;
  cleanliness: number;
}

// ========== PROMOTIONS ==========
export interface Promotion {
  id: string;
  tenant_id: string;
  shop_id: string;
  title: string;
  description: string;
  type: 'discount' | 'percentage' | 'bogo' | 'free_item' | 'loyalty';
  discount_value?: number;
  max_discount?: number;
  min_purchase?: number;
  valid_from: string;
  valid_until: string;
  usage_count: number;
  max_usage?: number;
  code?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromotionCreateRequest {
  shop_id: string;
  title: string;
  description: string;
  type: 'discount' | 'percentage' | 'bogo' | 'free_item' | 'loyalty';
  discount_value?: number;
  max_discount?: number;
  min_purchase?: number;
  valid_from: string;
  valid_until: string;
  max_usage?: number;
  code?: string;
}

// ========== NOTIFICATIONS ==========
export interface Notification {
  id: string;
  tenant_id: string;
  recipient_id: string;
  notification_type: 'reservation_confirmed' | 'promotion_available' | 'review_received' | 'other';
  title: string;
  message: string;
  reference_id?: string;
  reference_type?: string;
  is_read: boolean;
  read_at?: string;
  created_at: string;
  updated_at: string;
}

export interface NotificationCreateRequest {
  recipient_id: string;
  notification_type: string;
  title: string;
  message: string;
  reference_id?: string;
  reference_type?: string;
}

// ========== STATS ==========
export interface DashboardStats {
  total_reservations: number;
  total_customers: number;
  average_rating: number;
  total_reviews: number;
  recent_reservations: Reservation[];
  top_promotions: Promotion[];
}