import { useState, useEffect } from 'react';
import { reservationAPI } from '../services/api';
import type { Reservation, ReservationCreateRequest } from '../types';

export default function Reservations() {
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [formData, setFormData] = useState<Partial<ReservationCreateRequest>>({
    shop_id: 'shop_123',
    customer_id: 'customer_123',
    reservation_date: '',
    reservation_time: '',
    guest_count: 2,
    special_requests: '',
  });

  useEffect(() => {
    fetchReservations();
  }, []);

  const fetchReservations = async () => {
    try {
      setLoading(true);
      const res = await reservationAPI.listByShop('shop_123');
      setReservations(res.data || []);
    } catch (error) {
      console.error('Error fetching reservations:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await reservationAPI.create(formData);
      setFormData({
        shop_id: 'shop_123',
        customer_id: 'customer_123',
        reservation_date: '',
        reservation_time: '',
        guest_count: 2,
        special_requests: '',
      });
      setShowForm(false);
      fetchReservations();
    } catch (error) {
      console.error('Error creating reservation:', error);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await reservationAPI.delete(id);
      fetchReservations();
    } catch (error) {
      console.error('Error deleting reservation:', error);
    }
  };

  if (loading) {
    return <div className="page"><p>Loading reservations...</p></div>;
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>予約管理</h1>
        <button className="btn btn-primary" onClick={() => setShowForm(!showForm)}>
          ➕ 新規予約
        </button>
      </div>

      {showForm && (
        <form className="form" onSubmit={handleSubmit}>
          <input
            type="date"
            required
            value={formData.reservation_date || ''}
            onChange={(e) => setFormData({ ...formData, reservation_date: e.target.value })}
            placeholder="Date"
          />
          <input
            type="time"
            required
            value={formData.reservation_time || ''}
            onChange={(e) => setFormData({ ...formData, reservation_time: e.target.value })}
            placeholder="Time"
          />
          <input
            type="number"
            min="1"
            required
            value={formData.guest_count || 2}
            onChange={(e) => setFormData({ ...formData, guest_count: parseInt(e.target.value) })}
            placeholder="Guest count"
          />
          <textarea
            value={formData.special_requests || ''}
            onChange={(e) => setFormData({ ...formData, special_requests: e.target.value })}
            placeholder="Special requests"
          />
          <div className="form-actions">
            <button type="submit" className="btn btn-primary">Save</button>
            <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </form>
      )}

      <section className="section">
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Time</th>
                <th>Guests</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {reservations.map((res) => (
                <tr key={res.id}>
                  <td>{res.reservation_date}</td>
                  <td>{res.reservation_time}</td>
                  <td>{res.guest_count}</td>
                  <td>
                    <span className={`status-badge status-${res.status}`}>
                      {res.status}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => handleDelete(res.id)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
