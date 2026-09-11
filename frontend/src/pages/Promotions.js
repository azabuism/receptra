import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { promotionAPI } from '../services/api';
export default function Promotions() {
    const [promotions, setPromotions] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const [loading, setLoading] = useState(true);
    const [formData, setFormData] = useState({
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
        }
        catch (error) {
            console.error('Error fetching promotions:', error);
        }
        finally {
            setLoading(false);
        }
    };
    const handleSubmit = async (e) => {
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
        }
        catch (error) {
            console.error('Error creating promotion:', error);
        }
    };
    const handleDelete = async (id) => {
        try {
            await promotionAPI.delete(id);
            fetchPromotions();
        }
        catch (error) {
            console.error('Error deleting promotion:', error);
        }
    };
    if (loading) {
        return _jsx("div", { className: "page", children: _jsx("p", { children: "Loading promotions..." }) });
    }
    return (_jsxs("div", { className: "page", children: [_jsxs("div", { className: "page-header", children: [_jsx("h1", { children: "\u30D7\u30ED\u30E2\u30FC\u30B7\u30E7\u30F3\u7BA1\u7406" }), _jsx("button", { className: "btn btn-primary", onClick: () => setShowForm(!showForm), children: "\u2795 \u65B0\u898F\u30D7\u30ED\u30E2\u30FC\u30B7\u30E7\u30F3" })] }), showForm && (_jsxs("form", { className: "form", onSubmit: handleSubmit, children: [_jsx("input", { type: "text", required: true, value: formData.title || '', onChange: (e) => setFormData({ ...formData, title: e.target.value }), placeholder: "Title" }), _jsx("textarea", { required: true, value: formData.description || '', onChange: (e) => setFormData({ ...formData, description: e.target.value }), placeholder: "Description" }), _jsxs("select", { value: formData.type || 'discount', onChange: (e) => setFormData({ ...formData, type: e.target.value }), children: [_jsx("option", { value: "discount", children: "Discount" }), _jsx("option", { value: "percentage", children: "Percentage" }), _jsx("option", { value: "bogo", children: "Buy One Get One" }), _jsx("option", { value: "free_item", children: "Free Item" }), _jsx("option", { value: "loyalty", children: "Loyalty" })] }), _jsx("input", { type: "number", value: formData.discount_value || 0, onChange: (e) => setFormData({ ...formData, discount_value: parseFloat(e.target.value) }), placeholder: "Discount value" }), _jsx("input", { type: "datetime-local", required: true, value: formData.valid_from || '', onChange: (e) => setFormData({ ...formData, valid_from: e.target.value }), placeholder: "Valid from" }), _jsx("input", { type: "datetime-local", required: true, value: formData.valid_until || '', onChange: (e) => setFormData({ ...formData, valid_until: e.target.value }), placeholder: "Valid until" }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { type: "submit", className: "btn btn-primary", children: "Save" }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: () => setShowForm(false), children: "Cancel" })] })] })), _jsx("section", { className: "section", children: _jsx("div", { className: "cards-grid", children: promotions.map((promo) => (_jsxs("div", { className: "card", children: [_jsx("h3", { children: promo.title }), _jsx("p", { children: promo.description }), _jsxs("div", { className: "card-meta", children: [_jsx("span", { className: "badge", children: promo.type }), _jsxs("span", { className: "badge", children: ["Usage: ", promo.usage_count] })] }), _jsx("button", { className: "btn btn-sm btn-danger", onClick: () => handleDelete(promo.id), children: "Delete" })] }, promo.id))) }) })] }));
}
