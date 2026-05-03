# Untestable Items

- Forms and detailed APIs were not extracted effectively during automated crawl due to the heavily JavaScript-driven nature of the React application where explicit `<form>` and input tracking is hard to map natively without deeper source access.
- Safe Mode disabled testing of any destructive features as they could not be reliably isolated.
