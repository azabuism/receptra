import { useState, useEffect } from 'react';
import { promotionAPI } from '../services/api';
import type { Promotion, PromotionCreateRequest } from '../types';

export default function Promotions() {
  const [promotions, setPromotions] = useState<Promotion[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [formData, setFormData] = useState<Partial<PromotionCreateRequest>>({
    shop_id: 'shop_123',
    title: '',
    description: '',
    type: 'discount',
    discount_value: 0,
    valid_from: '',
    valid_until: '',
  });

  useEffect(() => {
    fetchPromotions();
  }, []);

  const fetchPromotions = async () => {
    try {
      setLoading(true);
      const res = await promotionAPI.listByShop('shop_123');
      setPromotions(res.data || []);
    } catch (error) {
      console.error('Error fetching promotions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await promotionAPI.create(formData);
      setFormData({
        shop_id: 'shop_123',
        title: '',
        description: '',
        type: 'discount',
        discount_value: 0,
        valid_from: '',
        valid_until: '',
      });
      setShowForm(false);
      fetchPromotions();
    } catch (error) {
      console.error('Error creating promotion:', error);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await promotionAPI.delete(id);
      fetchPromotions();
    } catch (error) {
      console.error('Error deleting promotion:', error);
    }
  };

  if (loading) {
    return <div className="page"><p>Loading promotions...</p></div>;
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>プロモーション管理</h1>
        <button className="btn btn-primary" onClick={() => setShowForm(!showForm)}>
          ➕ 新規プロモーション
        </button>
      </div>

      {showForm && (
        <form className="form" onSubmit={handleSubmit}>
          <input
            type="text"
            required
            value={formData.title || ''}
            onChange={(e) => setFormData({ ...formData, title: e.target.value })}
            placeholder="Title"
          />
          <textarea
            required
            value={formData.description || ''}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            placeholder="Description"
          />
          <select
            value={formData.type || 'discount'}
            onChange={(e) => setFormData({ ...formData, type: e.target.value as any })}
          >
            <option value="discount">Discount</option>
            <option value="percentage">Percentage</option>
            <option value="bogo">Buy One Get One</option>
            <option value="free_item">Free Item</option>
            <option value="loyalty">Loyalty</option>
          </select>
          <input
            type="number"
            value={formData.discount_value || 0}
            onChange={(e) => setFormData({ ...formData, discount_value: parseFloat(e.target.value) })}
            placeholder="Discount value"
          />
          <input
            type="datetime-local"
            required
            value={formData.valid_from || ''}
            onChange={(e) => setFormData({ ...formData, valid_from: e.target.value })}
            placeholder="Valid from"
          />
          <input
            type="datetime-local"
            required
            value={formData.valid_until || ''}
            onChange={(e) => setFormData({ ...formData, valid_until: e.target.value })}
            placeholder="Valid until"
          />
          <div className="form-actions">
            <button type="submit" className="btn btn-primary">Save</button>
            <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </form>
      )}

      <section className="section">
        <div className="cards-grid">
          {promotions.map((promo) => (
            <div key={promo.id} className="card">
              <h3>{promo.title}</h3>
              <p>{promo.description}</p>
              <div className="card-meta">
                <span className="badge">{promo.type}</span>
                <span className="badge">Usage: {promo.usage_count}</span>
              </div>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => handleDelete(promo.id)}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
