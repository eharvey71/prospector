export const metadata = { title: "Job Engine" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{
        margin: 0,
        background: "#15171c",
        color: "#e2e4e9",
        fontFamily: "system-ui, sans-serif",
        minHeight: "100vh",
      }}>
        {children}
      </body>
    </html>
  );
}
