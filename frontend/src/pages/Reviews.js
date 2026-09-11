import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { reviewAPI } from '../services/api';
export default function Reviews() {
    const [reviews, setReviews] = useState([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        fetchReviews();
    }, []);
    const fetchReviews = async () => {
        try {
            setLoading(true);
            const res = await reviewAPI.listByShop('shop_123');
            setReviews(res.data || []);
        }
        catch (error) {
            console.error('Error fetching reviews:', error);
        }
        finally {
            setLoading(false);
        }
    };
    const handleDelete = async (id) => {
        try {
            await reviewAPI.delete(id);
            fetchReviews();
        }
        catch (error) {
            console.error('Error deleting review:', error);
        }
    };
    const renderStars = (rating) => {
        return '⭐'.repeat(Math.round(rating));
    };
    if (loading) {
        return _jsx("div", { className: "page", children: _jsx("p", { children: "Loading reviews..." }) });
    }
    return (_jsxs("div", { className: "page", children: [_jsx("h1", { children: "\u30EC\u30D3\u30E5\u30FC\u30FB\u8A55\u4FA1" }), _jsx("section", { className: "section", children: _jsx("div", { className: "reviews-list", children: reviews.map((review) => (_jsxs("div", { className: "review-card", children: [_jsxs("div", { className: "review-header", children: [_jsx("h3", { children: review.title }), _jsx("span", { className: "stars", children: renderStars(review.rating) })] }), _jsx("p", { className: "review-content", children: review.content }), _jsx("div", { className: "review-meta", children: _jsxs("span", { children: ["\uD83D\uDC4D ", review.helpful_count, " helpful"] }) }), _jsx("button", { className: "btn btn-sm btn-danger", onClick: () => handleDelete(review.id), children: "Delete" })] }, review.id))) }) }), reviews.length === 0 && (_jsx("p", { className: "empty-state", children: "No reviews yet" }))] }));
}
