# Schema card: Chinook music store (SQLite)

Chinook is a fictional online music store. Customers buy single tracks; every purchase is an invoice with one line per track. Sales run from 2009-01-01 to 2013-12-22. Prices are in US dollars.

## Tables

### Album (347 rows)
- AlbumId INTEGER (primary key)
- Title NVARCHAR
- ArtistId INTEGER (-> Artist.ArtistId)

### Artist (275 rows)
- ArtistId INTEGER (primary key)
- Name NVARCHAR

### Customer (59 rows)
- CustomerId INTEGER (primary key)
- FirstName NVARCHAR
- LastName NVARCHAR
- Company NVARCHAR
- City NVARCHAR
- State NVARCHAR
- Country NVARCHAR: 'Argentina', 'Australia', 'Austria', 'Belgium', 'Brazil', 'Canada', 'Chile', 'Czech Republic', 'Denmark', 'Finland', 'France', 'Germany', 'Hungary', 'India', 'Ireland', 'Italy', 'Netherlands', 'Norway', 'Poland', 'Portugal', 'Spain', 'Sweden', 'USA', 'United Kingdom'
- SupportRepId INTEGER (-> Employee.EmployeeId)
- Not available (personal data): Address, PostalCode, Phone, Fax, Email

### Employee (8 rows)
- EmployeeId INTEGER (primary key)
- LastName NVARCHAR
- FirstName NVARCHAR
- Title NVARCHAR: 'General Manager', 'IT Manager', 'IT Staff', 'Sales Manager', 'Sales Support Agent'
- ReportsTo INTEGER (-> Employee.EmployeeId)
- HireDate DATETIME
- City NVARCHAR
- State NVARCHAR
- Country NVARCHAR
- Not available (personal data): BirthDate, Address, PostalCode, Phone, Fax, Email

### Genre (25 rows)
- GenreId INTEGER (primary key)
- Name NVARCHAR: 'Alternative', 'Alternative & Punk', 'Blues', 'Bossa Nova', 'Classical', 'Comedy', 'Drama', 'Easy Listening', 'Electronica/Dance', 'Heavy Metal', 'Hip Hop/Rap', 'Jazz', 'Latin', 'Metal', 'Opera', 'Pop', 'R&B/Soul', 'Reggae', 'Rock', 'Rock And Roll', 'Sci Fi & Fantasy', 'Science Fiction', 'Soundtrack', 'TV Shows', 'World'

### Invoice (412 rows)
- InvoiceId INTEGER (primary key)
- CustomerId INTEGER (-> Customer.CustomerId)
- InvoiceDate DATETIME
- BillingCity NVARCHAR
- BillingState NVARCHAR
- BillingCountry NVARCHAR
- Total NUMERIC
- Not available (personal data): BillingAddress, BillingPostalCode

### InvoiceLine (2,240 rows)
- InvoiceLineId INTEGER (primary key)
- InvoiceId INTEGER (-> Invoice.InvoiceId)
- TrackId INTEGER (-> Track.TrackId)
- UnitPrice NUMERIC
- Quantity INTEGER

### MediaType (5 rows)
- MediaTypeId INTEGER (primary key)
- Name NVARCHAR: 'AAC audio file', 'MPEG audio file', 'Protected AAC audio file', 'Protected MPEG-4 video file', 'Purchased AAC audio file'

### Playlist (18 rows)
- PlaylistId INTEGER (primary key)
- Name NVARCHAR: '90’s Music', 'Audiobooks', 'Brazilian Music', 'Classical', 'Classical 101 - Deep Cuts', 'Classical 101 - Next Steps', 'Classical 101 - The Basics', 'Grunge', 'Heavy Metal Classic', 'Movies', 'Music', 'Music Videos', 'On-The-Go 1', 'TV Shows'

### PlaylistTrack (8,715 rows)
- PlaylistId INTEGER (primary key, -> Playlist.PlaylistId)
- TrackId INTEGER (primary key, -> Track.TrackId)

### Track (3,503 rows)
- TrackId INTEGER (primary key)
- Name NVARCHAR
- AlbumId INTEGER (-> Album.AlbumId)
- MediaTypeId INTEGER (-> MediaType.MediaTypeId)
- GenreId INTEGER (-> Genre.GenreId)
- Composer NVARCHAR
- Milliseconds INTEGER
- Bytes INTEGER
- UnitPrice NUMERIC

## Metric definitions

- **revenue**: SUM(Invoice.Total), or SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity) at line level. Both give 2,328.60 in total.
- **orders**: COUNT(*) of Invoice rows (an order is an invoice).
- **average order value**: revenue / orders.
- **units sold**: SUM(InvoiceLine.Quantity). Quantity is always 1, so this is also the number of invoice lines.
- **active customers**: customers with at least one invoice in the period.
- **customer's sales rep**: Customer.SupportRepId -> Employee.EmployeeId.

## Things to know

- Dates are text like '2013-12-22 00:00:00'. Use strftime('%Y', InvoiceDate) for the year and strftime('%Y-%m', InvoiceDate) for the month.
- There is no cost, margin, marketing spend, stock or returns data: profit or ROI questions can't be answered.
- Track.UnitPrice is the list price (0.99 for audio, 1.99 for video). Use InvoiceLine.UnitPrice for what was paid.
- Invoice.BillingCountry equals Customer.Country on every invoice; either works for revenue by country.
- 1,519 of 3,503 tracks have never been sold; use LEFT JOIN from Track to find them.
- Track.Composer is NULL for 978 tracks, Customer.Company for 49 customers and Customer.State for 29.
- Playlist names are not unique (e.g. two 'Music' playlists); group by PlaylistId.
- Only the three Sales Support Agents (EmployeeId 3, 4, 5) have customers. Employee.ReportsTo is the manager's EmployeeId.
- SQLite has no MEDIAN or PERCENTILE function, and integer division truncates: multiply by 1.0 for ratios.

## Rules

- One read-only SELECT (or WITH ... SELECT) per answer; no recursive CTEs.
- Name the columns you need; SELECT * fails on tables with personal data.
