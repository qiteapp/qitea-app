export const activities = [
  {
    id: 'taste',
    title: 'Taste from Makkah',
    label: 'TASTE FROM MAKKAH',
    emoji: '🍗',
    items: [
      { name: 'Al Tazaj', actions: ['Directions', 'Order'] },
      { name: 'Al Baik', actions: ['Directions', 'Order'] },
    ],
  },
  {
    id: 'shopping',
    title: 'Go Shopping',
    label: 'GO SHOPPING',
    emoji: '🛍️',
    items: [
      { name: 'Makkah Mall', actions: ['Directions', 'Info'] },
      { name: 'Al Hijaz Mall', actions: ['Directions', 'Info'] },
    ],
  },
  {
    id: 'explore',
    title: 'Explore Makkah',
    label: 'EXPLORE MAKKAH',
    emoji: '🕋',
    items: [
      { name: 'Jabal Thour', actions: ['Directions', 'Info'] },
      { name: 'Kiswa Factory', actions: ['Directions', 'Info'] },
    ],
  },
  {
    id: 'gifts',
    title: 'Gifts Shop',
    label: 'GIFTS SHOP',
    emoji: '🎁',
    cart: true,
    items: [
      { name: 'Makkah Package', actions: ['Add to Cart'] },
      { name: 'Kaaba Necklace', actions: ['Add to Cart'] },
    ],
  },
]

export const services = [
  { id: 'pickup', name: 'Pick-Up', emoji: '🚐' },
  { id: 'hotels', name: 'Hotels', emoji: '🏨' },
  { id: 'transport', name: 'Public Transport', emoji: '🚌' },
  { id: 'guide', name: 'Tour Guide', emoji: '🧑‍💼' },
  { id: 'offline', name: 'Offline Guide', emoji: '🗺️' },
]

export const navSections = [
  { id: 'home', label: 'Home', emoji: '🏠' },
  { id: 'map', label: 'Map', emoji: '🗺️' },
  { id: 'services', label: 'Services', emoji: '🛎️' },
  { id: 'account', label: 'Account', emoji: '👤' },
]

export const menuSections = [...navSections, { id: 'settings', label: 'Settings', emoji: '⚙️' }]
