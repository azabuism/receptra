# RECEPTRA Multi-Business Booking Components

Frontend React components for the expanded RECEPTRA platform supporting restaurants, beauty salons, hotels, schools, and cram schools.

## Components Overview

### 1. ServiceSelector
Allows customers to select services offered by a shop.

**Props:**
- `shopId` (string, required): The ID of the shop
- `businessType` (string, required): The business type (restaurant, beauty, hotel, etc.)
- `onSelect` (function, required): Callback when a service is selected
- `selectedServiceId` (string, optional): ID of the currently selected service

**Usage:**
```jsx
<ServiceSelector 
  shopId="shop-123"
  businessType="beauty"
  onSelect={(service) => handleServiceSelect(service)}
  selectedServiceId={selectedId}
/>
```

**Example Services:**
- Beauty: ヘアカット（Hair Cut）, カラー（Color）, パーマ（Perm）
- Restaurant: ランチセット（Lunch Set）, ディナーコース（Dinner Course）
- Hotel: シングルルーム（Single Room）, ダブルルーム（Double Room）

---

### 2. StaffSelector
Allows customers to select a specific staff member/instructor/stylist.

**Props:**
- `shopId` (string, required): The ID of the shop
- `serviceId` (string, optional): Service ID for filtering staff who provide it
- `onSelect` (function, required): Callback when staff is selected
- `selectedStaffId` (string, optional): ID of the currently selected staff
- `isOptional` (boolean, default: true): Whether selecting staff is optional
- `filterByService` (boolean, default: false): Only show staff who provide the selected service

**Usage:**
```jsx
<StaffSelector 
  shopId="shop-123"
  serviceId="service-456"
  onSelect={(staff) => handleStaffSelect(staff)}
  selectedStaffId={selectedId}
  filterByService={true}
/>
```

**Features:**
- Display staff photos, names, positions, specialties
- Show star ratings based on average_rating
- Optional staff selection for businesses that don't require it
- Filter by service availability

---

### 3. TimeSlotPicker
Allows customers to select available time slots for appointments/reservations.

**Props:**
- `date` (Date, required): The selected date
- `durationMinutes` (number, default: 60): How long the reservation lasts
- `shopId` (string, required): The ID of the shop
- `serviceId` (string, optional): Service ID for duration calculation
- `staffId` (string, optional): Staff ID for availability checking
- `onSelect` (function, required): Callback when time is selected
- `selectedTime` (string, optional): Currently selected time (ISO string)

**Usage:**
```jsx
<TimeSlotPicker 
  date={new Date('2026-09-15')}
  durationMinutes={60}
  shopId="shop-123"
  staffId="staff-456"
  onSelect={(time) => handleTimeSelect(time)}
  selectedTime={selectedDateTime}
/>
```

**Features:**
- 30-minute interval slots
- Shows available times based on shop hours
- Conflicts with existing reservations are blocked
- Staff availability checking

---

### 4. DateRangePicker
Specialized component for hotel/accommodation bookings with check-in/check-out dates.

**Props:**
- `onSelect` (function, required): Callback with {checkIn, checkOut, nights}
- `minDate` (Date, default: today): Earliest selectable date
- `maxDate` (Date, optional): Latest selectable date
- `minNights` (number, default: 1): Minimum nights for stay
- `maxNights` (number, default: 30): Maximum nights for stay
- `selectedCheckIn` (Date, optional): Pre-selected check-in date
- `selectedCheckOut` (Date, optional): Pre-selected check-out date
- `blockedDates` (array, optional): Dates that cannot be selected

**Usage:**
```jsx
<DateRangePicker 
  onSelect={(range) => handleDateSelect(range)}
  minNights={1}
  maxNights={30}
  blockedDates={unavailableDates}
/>
```

**Features:**
- Visual calendar with hover preview
- Range highlighting
- Blocks past dates
- Validates minimum/maximum nights
- Blocked date visualization

---

### 5. AddOnsSelector
Allows selection of optional add-on services or extras.

**Props:**
- `addOns` (array, required): Array of add-on objects
- `onSelect` (function, required): Callback with selected add-ons
- `selectedAddOns` (array, default: []): Currently selected add-ons

**Add-On Object Structure:**
```javascript
{
  id: "addon-123",
  name: "シャンプー＆トリートメント",
  description: "頭皮クレンジング付き",
  price: 2000,
  quantity: 1
}
```

**Usage:**
```jsx
const addOns = [
  { id: "1", name: "シャンプー", price: 1000 },
  { id: "2", name: "トリートメント", price: 1500 },
  { id: "3", name: "ヘッドスパ", price: 2000 }
];

<AddOnsSelector 
  addOns={addOns}
  onSelect={(selected) => handleAddOnsSelect(selected)}
  selectedAddOns={selectedAddOns}
/>
```

**Features:**
- Checkbox-based selection
- Running total calculation
- Add-on descriptions and prices

---

### 6. ReservationPreview
Comprehensive preview component showing complete reservation details before confirmation.

**Props:**
- `reservation` (object, required): Reservation details
- `addOns` (array, default: []): Selected add-ons
- `onConfirm` (function, required): Callback when confirmed
- `isLoading` (boolean, default: false): Loading state

**Reservation Object Structure:**
```javascript
{
  shopName: "Hair Salon Bliss",
  businessType: "beauty",
  date: "2026-09-15T14:00:00",
  numberOfPeople: 4,
  serviceName: "ヘアカット",
  durationMinutes: 60,
  staffName: "田中太郎",
  checkIn: "2026-09-15",  // For hotels
  checkOut: "2026-09-17",  // For hotels
  nights: 2,  // For hotels
  basePrice: 5000,
  specialRequests: "できるだけ前髪は短くお願いします",
  paymentMethod: "credit_card"
}
```

**Usage:**
```jsx
<ReservationPreview 
  reservation={reservationDetails}
  addOns={selectedAddOns}
  onConfirm={() => submitReservation()}
  isLoading={isSubmitting}
/>
```

**Features:**
- Shop and service information display
- Add-ons breakdown
- Total price calculation
- Terms and conditions checkbox
- Confirmation button with disabled state management

---

### 7. BusinessTypeFilter
Filter component for searching shops by business type.

**Props:**
- `selectedTypes` (array, default: []): Currently selected business type IDs
- `onFilter` (function, required): Callback with selected types
- `showCounts` (boolean, default: true): Show shop count for each type

**Usage:**
```jsx
<BusinessTypeFilter 
  selectedTypes={selectedTypes}
  onFilter={(types) => handleFilterChange(types)}
  showCounts={true}
/>
```

**Business Types:**
- `restaurant` - 飲食店 (Restaurant)
- `beauty` - 美容院 (Beauty Salon)
- `hotel` - ホテル (Hotel)
- `school` - スクール (School)
- `cram_school` - 塾 (Cram School)
- `clinic` - 医院 (Clinic)
- `gym` - ジム (Gym)
- `other` - その他 (Other)

**Features:**
- Multi-select business types
- Live shop count display
- Visual selected state
- Quick filter reset

---

## CSS Styling

All components use the shared CSS file `components.css` which includes:
- Color variables and theme tokens
- Responsive design for mobile/tablet/desktop
- Hover and selected states
- Accessibility features
- Print-friendly styling

### Color Scheme
- Primary: `#2563eb` (Blue)
- Success: `#10b981` (Green)
- Danger: `#ef4444` (Red)
- Warning: `#f59e0b` (Amber)
- Neutral: Various grays from `#fafafa` to `#111827`

### Responsive Breakpoints
- Desktop: Full grid layouts
- Tablet (768px): Reduced grid columns
- Mobile (480px): Single column layouts, adjusted spacing

---

## Integration Guide

### 1. Install components into your React project:

```bash
# Copy component files to your project
cp ServiceSelector.jsx src/components/
cp StaffSelector.jsx src/components/
cp TimeSlotPicker.jsx src/components/
cp DateRangePicker.jsx src/components/
cp AddOnsSelector.jsx src/components/
cp ReservationPreview.jsx src/components/
cp BusinessTypeFilter.jsx src/components/

# Copy CSS
cp components.css src/styles/
```

### 2. Import components in your pages:

```jsx
import ServiceSelector from '../components/ServiceSelector';
import StaffSelector from '../components/StaffSelector';
import ReservationPreview from '../components/ReservationPreview';
import BusinessTypeFilter from '../components/BusinessTypeFilter';
```

### 3. Update your API endpoints:

Make sure your API provides these endpoints (implemented in backend):

```
GET /api/v1/services?shop_id={id}&business_type={type}
GET /api/v1/staff?shop_id={id}
GET /api/v1/staff/{id}/services
POST /api/v1/reservations
```

### 4. Example booking flow:

```jsx
import { useState } from 'react';
import ServiceSelector from './components/ServiceSelector';
import StaffSelector from './components/StaffSelector';
import TimeSlotPicker from './components/TimeSlotPicker';
import ReservationPreview from './components/ReservationPreview';

function BookingFlow() {
  const [selectedService, setSelectedService] = useState(null);
  const [selectedStaff, setSelectedStaff] = useState(null);
  const [selectedDate, setSelectedDate] = useState(null);
  const [selectedTime, setSelectedTime] = useState(null);
  const shopId = 'shop-123';
  const businessType = 'beauty';

  return (
    <div className="booking-flow">
      <ServiceSelector 
        shopId={shopId}
        businessType={businessType}
        onSelect={setSelectedService}
      />
      
      {selectedService && (
        <StaffSelector 
          shopId={shopId}
          serviceId={selectedService.id}
          onSelect={setSelectedStaff}
          filterByService={true}
        />
      )}
      
      {selectedService && (
        <TimeSlotPicker 
          date={selectedDate}
          shopId={shopId}
          staffId={selectedStaff?.id}
          durationMinutes={selectedService.duration_minutes}
          onSelect={setSelectedTime}
        />
      )}
      
      {selectedService && selectedTime && (
        <ReservationPreview 
          reservation={{
            shopName: 'Hair Salon Bliss',
            serviceName: selectedService.name,
            staffName: selectedStaff?.name,
            date: selectedTime,
            basePrice: selectedService.base_price
          }}
          onConfirm={() => submitReservation()}
        />
      )}
    </div>
  );
}
```

---

## API Integration Notes

### Service Data Flow
1. **ServiceSelector** fetches from: `GET /api/v1/services?shop_id=X&business_type=Y`
2. **StaffSelector** fetches from: `GET /api/v1/staff?shop_id=X`
3. **TimeSlotPicker** checks: `GET /api/v1/reservations?shop_id=X&date=Y`
4. **ReservationPreview** submits: `POST /api/v1/reservations`

### Required API Response Formats

**Service Response:**
```json
[
  {
    "id": "service-123",
    "name": "ヘアカット",
    "description": "シャンプー付き",
    "base_price": 5000,
    "duration_minutes": 60,
    "service_type": "cut"
  }
]
```

**Staff Response:**
```json
[
  {
    "id": "staff-123",
    "name": "田中太郎",
    "position": "スタイリスト",
    "specialty": "カット・カラー",
    "photo_url": "https://...",
    "average_rating": 4.5,
    "bio": "経験10年以上の...",
    "total_reservations": 250
  }
]
```

---

## Testing Components

Each component includes built-in error handling and loading states:

```jsx
// Loading state
<ServiceSelector /> // Shows "読み込み中..."

// Error state
// Component displays error message if API fails

// Empty state
// Component shows "利用可能なサービスがありません"
```

---

## Accessibility Features

- Semantic HTML with proper form controls
- ARIA labels and roles
- Keyboard navigation support
- Color contrast compliant
- Touch-friendly button sizes (minimum 48px)

---

## Browser Support

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- Mobile browsers (iOS Safari 14+, Chrome Android)

---

## Performance Notes

- Components use React.memo for optimization
- Lazy loading for images (staff photos)
- Debounced API calls in search filters
- Virtualization recommended for large lists (100+ items)

---

## Future Enhancements

- [ ] Multi-language support
- [ ] Dark mode support
- [ ] Accessibility improvements (WCAG AAA)
- [ ] Animation transitions
- [ ] Advanced filtering options
- [ ] Booking calendar view
- [ ] Mobile app integration
