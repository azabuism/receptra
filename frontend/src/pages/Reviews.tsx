import { useState, useEffect } from 'react';
import { reviewAPI } from '../services/api';
import type { Review } from '../types';

export default function Reviews() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchReviews();
  }, []);

  const fetchReviews = async () => {
    try {
      setLoading(true);
      const res = await reviewAPI.listByShop('shop_123');
      setReviews(res.data || []);
    } catch (error) {
      console.error('Error fetching reviews:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await reviewAPI.delete(id);
      fetchReviews();
    } catch (error) {
      console.error('Error deleting review:', error);
    }
  };

  const renderStars = (rating: number) => {
    return '⭐'.repeat(Math.round(rating));
  };

  if (loading) {
    return <div className="page"><p>Loading reviews...</p></div>;
  }

  return (
    <div className="page">
      <h1>レビュー・評価</h1>

      <section className="section">
        <div className="reviews-list">
          {reviews.map((review) => (
            <div key={review.id} className="review-card">
              <div className="review-header">
                <h3>{review.title}</h3>
                <span className="stars">{renderStars(review.rating)}</span>
              </div>
              <p className="review-content">{review.content}</p>
              <div className="review-meta">
                <span>👍 {review.helpful_count} helpful</span>
              </div>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => handleDelete(review.id)}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      </section>

      {reviews.length === 0 && (
        <p className="empty-state">No reviews yet</p>
      )}
    </div>
  );
}
