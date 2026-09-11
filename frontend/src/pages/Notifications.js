import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { notificationAPI } from '../services/api';
export default function Notifications() {
    const [notifications, setNotifications] = useState([]);
    const [unreadOnly, setUnreadOnly] = useState(false);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        fetchNotifications();
    }, [unreadOnly]);
    const fetchNotifications = async () => {
        try {
            setLoading(true);
            const res = await notificationAPI.list({
                recipient_id: 'test_recipient_123',
                unread_only: unreadOnly,
            });
            setNotifications(res.data?.items || []);
        }
        catch (error) {
            console.error('Error fetching notifications:', error);
        }
        finally {
            setLoading(false);
        }
    };
    const handleMarkAsRead = async (id, isRead) => {
        try {
            await notificationAPI.markAsRead(id, { is_read: !isRead });
            fetchNotifications();
        }
        catch (error) {
            console.error('Error marking notification:', error);
        }
    };
    const handleDelete = async (id) => {
        try {
            await notificationAPI.delete(id);
            fetchNotifications();
        }
        catch (error) {
            console.error('Error deleting notification:', error);
        }
    };
    const getNotificationIcon = (type) => {
        switch (type) {
            case 'reservation_confirmed':
                return '📅';
            case 'promotion_available':
                return '🎉';
            case 'review_received':
                return '⭐';
            default:
                return '🔔';
        }
    };
    if (loading) {
        return _jsx("div", { className: "page", children: _jsx("p", { children: "Loading notifications..." }) });
    }
    return (_jsxs("div", { className: "page", children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { children: "\u901A\u77E5" }), _jsxs("label", { className: "checkbox", children: [_jsx("input", { type: "checkbox", checked: unreadOnly, onChange: (e) => setUnreadOnly(e.target.checked) }), "\u672A\u8AAD\u306E\u307F\u8868\u793A"] })] }), _jsx("section", { className: "section", children: _jsx("div", { className: "notifications-list", children: notifications.map((notif) => (_jsxs("div", { className: `notification-item ${notif.is_read ? 'read' : 'unread'}`, children: [_jsx("div", { className: "notification-icon", children: getNotificationIcon(notif.notification_type) }), _jsxs("div", { className: "notification-content", children: [_jsx("h4", { children: notif.title }), _jsx("p", { children: notif.message }), _jsx("small", { children: new Date(notif.created_at).toLocaleString() })] }), _jsxs("div", { className: "notification-actions", children: [_jsx("button", { className: `btn btn-sm ${notif.is_read ? 'btn-secondary' : 'btn-primary'}`, onClick: () => handleMarkAsRead(notif.id, notif.is_read), children: notif.is_read ? '✓ Read' : 'Mark Read' }), _jsx("button", { className: "btn btn-sm btn-danger", onClick: () => handleDelete(notif.id), children: "\u2715" })] })] }, notif.id))) }) }), notifications.length === 0 && (_jsx("p", { className: "empty-state", children: "No notifications" }))] }));
}
