import { useState, useEffect } from 'react';
import { notificationAPI } from '../services/api';
import type { Notification } from '../types';

export default function Notifications() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
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
    } catch (error) {
      console.error('Error fetching notifications:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleMarkAsRead = async (id: string, isRead: boolean) => {
    try {
      await notificationAPI.markAsRead(id, { is_read: !isRead });
      fetchNotifications();
    } catch (error) {
      console.error('Error marking notification:', error);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await notificationAPI.delete(id);
      fetchNotifications();
    } catch (error) {
      console.error('Error deleting notification:', error);
    }
  };

  const getNotificationIcon = (type: string) => {
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
    return <div className="page"><p>Loading notifications...</p></div>;
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>通知</h1>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
          />
          未読のみ表示
        </label>
      </div>

      <section className="section">
        <div className="notifications-list">
          {notifications.map((notif) => (
            <div key={notif.id} className={`notification-item ${notif.is_read ? 'read' : 'unread'}`}>
              <div className="notification-icon">
                {getNotificationIcon(notif.notification_type)}
              </div>
              <div className="notification-content">
                <h4>{notif.title}</h4>
                <p>{notif.message}</p>
                <small>{new Date(notif.created_at).toLocaleString()}</small>
              </div>
              <div className="notification-actions">
                <button
                  className={`btn btn-sm ${notif.is_read ? 'btn-secondary' : 'btn-primary'}`}
                  onClick={() => handleMarkAsRead(notif.id, notif.is_read)}
                >
                  {notif.is_read ? '✓ Read' : 'Mark Read'}
                </button>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={() => handleDelete(notif.id)}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {notifications.length === 0 && (
        <p className="empty-state">No notifications</p>
      )}
    </div>
  );
}
