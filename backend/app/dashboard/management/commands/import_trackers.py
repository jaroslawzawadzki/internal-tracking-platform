import requests
from django.core.management.base import BaseCommand
from dashboard.models import TrackerLibrary

class Command(BaseCommand):
    help = 'Importuje domeny i ciasteczka bez błędów o duplikaty'

    def handle(self, *args, **options):
        # 1/2: DOMENY
        self.stdout.write("1/2: Aktualizacja domen...")
        url_domains = "https://raw.githubusercontent.com/disconnectme/disconnect-tracking-protection/master/services.json"
        try:
            res_d = requests.get(url_domains, timeout=15).json()
            count_d = 0
            for cat_name, services_list in res_d.get('categories', {}).items():
                for service_dict in services_list:
                    if not isinstance(service_dict, dict): continue
                    for s_name, details in service_dict.items():
                        for key, domains in details.items():
                            if isinstance(domains, list):
                                for d in domains:
                                    # Używamy update_or_create zamiast get_or_create
                                    TrackerLibrary.objects.update_or_create(
                                        domain=d,
                                        defaults={
                                            'name': s_name,
                                            'category': cat_name,
                                            'description_pl': f"Usługa zewnętrzna: {cat_name}."
                                        }
                                    )
                                    count_d += 1
            self.stdout.write(self.style.SUCCESS(f"Domeny gotowe! Przetworzono: {count_d}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Błąd domen: {e}"))

        # 2/2: CIASTECZKA
        self.stdout.write("2/2: Aktualizacja bazy ciasteczek...")
        url_cookies = "https://raw.githubusercontent.com/jkwakman/Open-Cookie-Database/master/open-cookie-database.json"
        try:
            data = requests.get(url_cookies, timeout=15).json()
            count_c = 0
            for service_name, cookies_list in data.items():
                if isinstance(cookies_list, list):
                    for c in cookies_list:
                        if not isinstance(c, dict): continue
                        c_name = c.get('cookie')
                        description = c.get('description', 'Brak opisu')
                        if not c_name: continue

                        # Tutaj też update_or_create - jeśli domena już istnieje (np. betweendigital.com),
                        # to po prostu nadpiszemy ją danymi z bazy ciasteczek.
                        TrackerLibrary.objects.update_or_create(
                            domain=c_name,
                            defaults={
                                'name': service_name,
                                'category': 'Cookie',
                                'description_pl': f"Opis: {description}"
                            }
                        )
                        count_c += 1
            self.stdout.write(self.style.SUCCESS(f"Ciasteczka gotowe! Przetworzono: {count_c}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Błąd ciasteczek: {e}"))