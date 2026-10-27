# Data collection guide

The planner is only as good as its data. Aim for **30 to 50 hotels** across Hunza, Skardu and Gilgit, and every main route
(Islamabad, Gilgit, Hunza, Skardu), with an honest `confidence` and `source` on every row.

1. Fill `docs/data_collection_template.xlsx` (read its "How to fill" sheet first).
2. Save each sheet as CSV UTF-8 into `data/` as `hotels_sample.csv` and `transport_sample.csv`.
3. In the app open **Admin** (needs `ADMIN_PASSWORD` in `.env`) and press **Refresh data now**. Rejected rows are listed with their line number.
4. Hotels maintained in the app (added by the admin, or edited by their owner) are never overwritten by a CSV refresh.

Where to look: hotel websites and Facebook pages, Google Maps listings (check the date of reviews), booking sites (screenshot the
date), calls or WhatsApp messages to hotels, and transport company ticket offices. Keep a note of the date and the person or page.

Confidence labels: **Verified** (you confirmed it directly), **Needs reconfirmation** (older or indirect source), **Estimated**
(rough figure), **Unknown** (cannot check). A Verified row older than 60 days is shown as Needs reconfirmation automatically.
