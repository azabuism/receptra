/**
 * RECEPTRA API Client
 * Handles communication with FastAPI backend with localStorage fallback
 */

class RECEPTRAAPIClient {
    constructor(baseURL = 'http://localhost:8000', useLocalStorage = true) {
        this.baseURL = baseURL;
        this.useLocalStorage = useLocalStorage;
        this.token = sessionStorage.getItem('receptra_auth_token') || null;
        this.shopId = sessionStorage.getItem('receptra_shop_id') || null;
    }

    // ========== Authentication ==========
    setAuth(shopId, token) {
        this.shopId = shopId;
        this.token = token;
        sessionStorage.setItem('receptra_shop_id', shopId);
        sessionStorage.setItem('receptra_auth_token', token);
    }

    // ========== HTTP Helper ==========
    async request(method, endpoint, data = null) {
        try {
            const url = `${this.baseURL}${endpoint}`;
            const options = {
                method,
                headers: {
                    'Content-Type': 'application/json',
                }
            };

            if (this.token) {
                options.headers['Authorization'] = `Bearer ${this.token}`;
            }

            if (data) {
                options.body = JSON.stringify(data);
            }

            const response = await fetch(url, options);
            if (!response.ok) {
                throw new Error(`API Error: ${response.status} ${response.statusText}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API Request Error:', error);
            if (this.useLocalStorage) {
                console.log('Falling back to localStorage');
                return { error: error.message };
            }
            throw error;
        }
    }

    // ========== Reminders API ==========
    async scheduleReminder(reminderType, triggerDaysBefore, enabled = true) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode - store locally
            return this.scheduleReminderLocal(reminderType, triggerDaysBefore, enabled);
        }

        const data = {
            shop_id: this.shopId,
            reminder_type: reminderType,
            trigger_days_before: triggerDaysBefore,
            enabled: enabled
        };

        return await this.request('POST', '/api/reminders/schedule', data);
    }

    scheduleReminderLocal(reminderType, triggerDaysBefore, enabled) {
        const reminder = {
            id: 'rem_' + Math.random().toString(36).substr(2, 9),
            type: reminderType,
            days: triggerDaysBefore,
            enabled: enabled,
            createdAt: new Date().toISOString()
        };

        const reminders = JSON.parse(localStorage.getItem('receptra_reminders') || '[]');
        reminders.push(reminder);
        localStorage.setItem('receptra_reminders', JSON.stringify(reminders));

        return { success: true, reminder_id: reminder.id };
    }

    async getReminderForReservation(reservationId) {
        return await this.request('GET', `/api/reminders/${reservationId}`);
    }

    async recordReminderResponse(reminderId, responses, callStatus) {
        const data = {
            reminder_id: reminderId,
            responses: responses,
            call_status: callStatus
        };

        return await this.request('PUT', `/api/reminders/${reminderId}/response`, data);
    }

    // ========== Business Calls API ==========
    async logBusinessCall(callerName, callerNumber, purpose, urgencyLevel = 'low', duration = null, transcription = null) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode
            return this.logBusinessCallLocal(callerName, callerNumber, purpose, urgencyLevel, duration, transcription);
        }

        const data = {
            shop_id: this.shopId,
            caller_name: callerName,
            caller_number: callerNumber,
            purpose: purpose,
            urgency_level: urgencyLevel,
            duration: duration,
            transcription: transcription
        };

        return await this.request('POST', '/api/business-calls/log', data);
    }

    logBusinessCallLocal(callerName, callerNumber, purpose, urgencyLevel = 'low', duration = null, transcription = null) {
        const call = {
            id: 'call_' + Math.random().toString(36).substr(2, 9),
            caller_name: callerName,
            caller_number: callerNumber,
            purpose: purpose,
            urgency_level: urgencyLevel,
            duration: duration,
            timestamp: new Date().toISOString(),
            transcription: transcription
        };

        const calls = JSON.parse(localStorage.getItem('receptra_business_calls') || '[]');
        calls.push(call);
        localStorage.setItem('receptra_business_calls', JSON.stringify(calls));

        return { success: true, call_id: call.id };
    }

    async getBusinessCalls(filters = {}) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode
            return this.getBusinessCallsLocal(filters);
        }

        const params = new URLSearchParams({
            shop_id: this.shopId,
            ...filters
        });

        return await this.request('GET', `/api/business-calls?${params.toString()}`);
    }

    getBusinessCallsLocal(filters = {}) {
        let calls = JSON.parse(localStorage.getItem('receptra_business_calls') || '[]');

        if (filters.urgency_level) {
            calls = calls.filter(c => c.urgency_level === filters.urgency_level);
        }
        if (filters.purpose) {
            calls = calls.filter(c => c.purpose === filters.purpose);
        }

        return { business_calls: calls };
    }

    async getBusinessCallDetail(callId) {
        return await this.request('GET', `/api/business-calls/${callId}`);
    }

    // ========== Urgent Notifications API ==========
    async createUrgentNotification(notificationType, content, urgency = 'medium', reservationId = null) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode
            return this.createUrgentNotificationLocal(notificationType, content, urgency, reservationId);
        }

        const data = {
            shop_id: this.shopId,
            reservation_id: reservationId,
            notification_type: notificationType,
            content: content,
            urgency: urgency
        };

        return await this.request('POST', '/api/notifications/urgent', data);
    }

    createUrgentNotificationLocal(notificationType, content, urgency = 'medium', reservationId = null) {
        const notification = {
            id: 'notif_' + Math.random().toString(36).substr(2, 9),
            type: notificationType,
            content: content,
            urgency: urgency,
            status: 'pending',
            timestamp: new Date().toISOString(),
            reservation_id: reservationId
        };

        const notifications = JSON.parse(localStorage.getItem('receptra_notifications') || '[]');
        notifications.push(notification);
        localStorage.setItem('receptra_notifications', JSON.stringify(notifications));

        return { success: true, notification_id: notification.id };
    }

    async getUrgentNotifications(filters = {}) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode
            return this.getUrgentNotificationsLocal(filters);
        }

        const params = new URLSearchParams({
            shop_id: this.shopId,
            ...filters
        });

        return await this.request('GET', `/api/notifications/urgent?${params.toString()}`);
    }

    getUrgentNotificationsLocal(filters = {}) {
        let notifications = JSON.parse(localStorage.getItem('receptra_notifications') || '[]');

        if (filters.status) {
            notifications = notifications.filter(n => n.status === filters.status);
        }

        return { notifications: notifications };
    }

    async acknowledgeNotification(notificationId) {
        if (this.useLocalStorage && !this.shopId) {
            // Demo mode
            return this.acknowledgeNotificationLocal(notificationId);
        }

        return await this.request('PUT', `/api/notifications/urgent/${notificationId}/acknowledge`);
    }

    acknowledgeNotificationLocal(notificationId) {
        const notifications = JSON.parse(localStorage.getItem('receptra_notifications') || '[]');
        const notification = notifications.find(n => n.id === notificationId);
        if (notification) {
            notification.status = 'acknowledged';
            localStorage.setItem('receptra_notifications', JSON.stringify(notifications));
            return { success: true };
        }
        return { error: 'Notification not found' };
    }

    // ========== IVR API ==========
    async handleBusinessCallMenu(callSid, digits) {
        const data = {
            call_sid: callSid,
            digits: digits
        };

        return await this.request('POST', '/api/ivr/business_menu', data);
    }

    // ========== Utility Methods ==========
    async testConnection() {
        try {
            const response = await this.request('GET', '/api/health');
            return response.status === 'ok';
        } catch (error) {
            return false;
        }
    }

    isAuthenticated() {
        return !!this.token && !!this.shopId;
    }

    clearAuth() {
        this.token = null;
        this.shopId = null;
        sessionStorage.removeItem('receptra_auth_token');
        sessionStorage.removeItem('receptra_shop_id');
    }
}

// Create global instance
window.RECEPTRAClient = new RECEPTRAAPIClient();
