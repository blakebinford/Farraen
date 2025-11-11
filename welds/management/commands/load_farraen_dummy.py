import random
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from drive.models import FileNode, FileVersion, Folder
from organizations.models import Organization
from projects.models import Project
from welds.models import MaterialHeat, NDERig, Welder, Weld


class Command(BaseCommand):
    help = "Seed a realistic demo dataset for Farraen weld tracking"

    def add_arguments(self, parser):
        parser.add_argument("--org", default="Farraen", help="Organization name to seed")
        parser.add_argument("--project", default="Demo Pipeline Loop", help="Project name to seed")
        parser.add_argument("--welds", type=int, default=300, help="Number of welds to seed")
        parser.add_argument("--days", type=int, default=19, help="Number of consecutive days to spread welds across")
        parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic data generation")

    def handle(self, *args, **options):
        org_name: str = options["org"].strip()
        project_name: str = options["project"].strip()
        weld_target: int = max(1, int(options["welds"]))
        day_span: int = max(1, int(options["days"]))
        seed_value: int = int(options["seed"])

        rng = random.Random(seed_value)

        with transaction.atomic():
            user = self._ensure_seed_user()
            org = self._ensure_org(org_name, user)
            project = self._ensure_project(project_name, org, user)

            folders = self._ensure_project_folders(org, project, user)
            wps_documents = self._ensure_wps_documents(org, project, folders["WPS"], user)
            heats = self._ensure_material_heats(org, project, folders["MTR"], wps_documents, rng, user)
            welders = self._ensure_welders(org, wps_documents, rng)
            rigs = self._ensure_nde_rigs(org, project, folders["Inspector Qualification"], rng)
            drawings = self._ensure_drawing_documents(org, project, folders["Drawings"], user)

            self._seed_welds(
                project=project,
                heats=heats,
                welders=welders,
                rigs=rigs,
                drawings=drawings,
                rng=rng,
                user=user,
                weld_target=weld_target,
                day_span=day_span,
            )

        self.stdout.write(self.style.SUCCESS("Farraen demo data loaded."))

    # ------------------------------------------------------------------
    # Helpers for ensuring prerequisite records exist
    # ------------------------------------------------------------------
    def _ensure_seed_user(self):
        User = get_user_model()
        user = User.objects.filter(is_superuser=True).order_by("id").first()
        if user:
            return user
        user = User.objects.order_by("id").first()
        if user:
            return user
        return User.objects.create_superuser(
            email="admin@farraen.local", password="admin123", first_name="Seed", last_name="Admin"
        )

    def _ensure_org(self, name, user):
        slug = slugify(name) or "farraen"
        org, created = Organization.objects.get_or_create(
            name=name,
            defaults={"slug": slug, "owner": user},
        )
        if created:
            org.owner = user
            org.save(update_fields=["owner"])
        elif org.owner_id is None and user:
            org.owner = user
            org.save(update_fields=["owner"])
        return org

    def _ensure_project(self, name, org, user):
        slug = slugify(name) or "demo-pipeline-loop"
        project, created = Project.objects.get_or_create(
            org=org,
            slug=slug,
            defaults={"name": name, "created_by": user},
        )
        if created:
            project.name = name
            project.created_by = user
            project.save(update_fields=["name", "created_by"])
        else:
            updated_fields = []
            if project.name != name:
                project.name = name
                updated_fields.append("name")
            if user and project.created_by_id is None:
                project.created_by = user
                updated_fields.append("created_by")
            if updated_fields:
                project.save(update_fields=updated_fields)
        return project

    def _ensure_project_folders(self, org, project, user):
        folder_names = [
            "Inspector Qualification",
            "WPS",
            "MTR",
            "Drawings",
            "NDE Reports",
        ]
        folders = {}
        for name in folder_names:
            slug = slugify(name) or name.lower().replace(" ", "-")
            folder, created = Folder.objects.get_or_create(
                org=org,
                project=project,
                parent=None,
                slug=slug,
                defaults={"name": name, "created_by": user},
            )
            if created and folder.name != name:
                folder.name = name
                folder.created_by = user
                folder.save(update_fields=["name", "created_by"])
            elif not created:
                updates = []
                if folder.name != name:
                    folder.name = name
                    updates.append("name")
                if user and folder.created_by_id is None:
                    folder.created_by = user
                    updates.append("created_by")
                if updates:
                    folder.save(update_fields=updates)
            folders[name] = folder
        return folders

    def _ensure_wps_documents(self, org, project, folder, user):
        wps_data = [
            ("WPS-001", "Carbon Steel Butt Weld"),
            ("WPS-002", "Branch Tie-In Procedure"),
            ("WPS-003", "Socket Weld Assembly"),
        ]
        documents = {}
        for number, title in wps_data:
            name = f"{number}.pdf"
            slug = slugify(name) or number.lower()
            defaults = {
                "name": name,
                "doc_type": FileNode.DocType.WPS,
                "number": number,
                "title": title,
                "project": project,
                "created_by": user,
            }
            file_node, created = FileNode.objects.get_or_create(
                org=org,
                folder=folder,
                slug=slug,
                defaults=defaults,
            )
            updates = []
            for field, value in defaults.items():
                if getattr(file_node, field) != value and not (field == "created_by" and not value):
                    setattr(file_node, field, value)
                    updates.append(field)
            if updates:
                file_node.save(update_fields=updates)
            self._ensure_placeholder_version(file_node, user)
            documents[number] = file_node
        return documents

    def _ensure_material_heats(self, org, project, mtr_folder, wps_documents, rng, user):
        grade_options = ["API 5L X52", "API 5L X65", "ASTM A106 Gr.B", "ASTM A53 Gr.B"]
        od_options = [Decimal("6.625"), Decimal("8.625"), Decimal("10.750"), Decimal("12.750"), Decimal("16.000")]
        wt_options = [Decimal("0.280"), Decimal("0.322"), Decimal("0.365"), Decimal("0.500"), Decimal("0.688")]

        existing_heats = MaterialHeat.objects.filter(org=org).order_by("heat_number")
        heat_map = {heat.heat_number: heat for heat in existing_heats}

        desired_count = rng.randint(10, 14)
        generated_numbers = list(heat_map.keys())
        while len(generated_numbers) < desired_count:
            prefix = rng.choice(["A", "H", "K", "L", "P", "R"])
            numeric = rng.randint(1000, 9999)
            suffix = rng.randint(10, 99)
            heat_number = f"{prefix}{numeric}-{suffix}"
            if heat_number not in heat_map and heat_number not in generated_numbers:
                generated_numbers.append(heat_number)

        heats = []
        for heat_number in sorted(generated_numbers):
            grade = rng.choice(grade_options)
            od = rng.choice(od_options)
            wt = rng.choice(wt_options)
            wps_number = rng.choice(list(wps_documents.keys()))
            mtr_name = f"MTR-{heat_number}.pdf"
            mtr_slug = slugify(mtr_name) or heat_number.lower()
            file_defaults = {
                "name": mtr_name,
                "doc_type": FileNode.DocType.MTR,
                "number": heat_number,
                "title": f"Material Test Report for {grade}",
                "project": project,
                "created_by": user,
            }
            file_node, created = FileNode.objects.get_or_create(
                org=org,
                folder=mtr_folder,
                slug=mtr_slug,
                defaults=file_defaults,
            )
            updates = []
            for field, value in file_defaults.items():
                if getattr(file_node, field) != value and not (field == "created_by" and not value):
                    setattr(file_node, field, value)
                    updates.append(field)
            if updates:
                file_node.save(update_fields=updates)
            self._ensure_placeholder_version(file_node, user)

            material_defaults = {
                "description": f"{grade} pipe heat {heat_number}",
                "material_grade": grade,
                "outer_diameter_in": od,
                "wall_thickness_in": wt,
                "wps_number": wps_number,
                "mtr_document": file_node,
                "wps_document": wps_documents.get(wps_number),
                "is_active": True,
            }
            material_heat, created = MaterialHeat.objects.get_or_create(
                org=org,
                heat_number=heat_number,
                defaults=material_defaults,
            )
            if not created:
                changed_fields = []
                for field, value in material_defaults.items():
                    if getattr(material_heat, field) != value:
                        setattr(material_heat, field, value)
                        changed_fields.append(field)
                if changed_fields:
                    material_heat.save(update_fields=changed_fields)
            heats.append(material_heat)
        heats.sort(key=lambda h: h.heat_number)
        return heats

    def _ensure_welders(self, org, wps_documents, rng):
        welder_names = [
            "J. Martinez",
            "S. Patel",
            "L. Chen",
            "B. O'Neil",
            "K. Yamamoto",
            "R. Johnson",
            "A. Hernandez",
            "M. Kowalski",
        ]
        wps_nodes = list(wps_documents.values())
        welders = []
        for idx, name in enumerate(welder_names, start=1):
            stencil = f"W{idx:03d}"
            defaults = {"name": name, "is_active": True}
            welder, created = Welder.objects.get_or_create(
                org=org,
                stencil=stencil,
                defaults=defaults,
            )
            if not created and welder.name != name:
                welder.name = name
                welder.save(update_fields=["name"])
            approved_count = rng.randint(1, min(3, len(wps_nodes)))
            approved = rng.sample(wps_nodes, approved_count)
            welder.approved_wps.set(approved)
            welders.append(welder)
        welders.sort(key=lambda w: w.stencil)
        return welders

    def _ensure_nde_rigs(self, org, project, inspector_folder, rng):
        possible_rigs = ["Rig-Alpha", "Rig-Bravo"]
        rig_count = rng.randint(1, len(possible_rigs))
        rigs = []
        for name in possible_rigs[:rig_count]:
            defaults = {"description": f"{name} ultrasonic crawler"}
            rig, created = NDERig.objects.get_or_create(
                org=org,
                project=project,
                name=name,
                defaults=defaults,
            )
            if not created and not rig.description:
                rig.description = defaults["description"]
                rig.save(update_fields=["description"])
            if not rig.qualification_folder_id:
                child_folder, _ = Folder.objects.get_or_create(
                    org=org,
                    project=project,
                    parent=inspector_folder,
                    slug=slugify(name) or name.lower(),
                    defaults={"name": name, "created_by": inspector_folder.created_by},
                )
                if rig.qualification_folder_id != child_folder.id:
                    rig.qualification_folder = child_folder
                    rig.save(update_fields=["qualification_folder"])
            rigs.append(rig)
        return rigs

    def _ensure_drawing_documents(self, org, project, drawing_folder, user):
        line_numbers = [1104, 1206, 1250, 1310, 1420]
        sheet_numbers = ["01", "02", "03", "04", "05"]
        drawings = []
        for line in line_numbers:
            for sheet in sheet_numbers:
                number = f"ISO-{line}-{sheet}"
                name = f"{number}.pdf"
                slug = slugify(name) or number.lower()
                defaults = {
                    "name": name,
                    "doc_type": "",
                    "number": number,
                    "title": f"Isometric {number}",
                    "project": project,
                    "created_by": user,
                }
                file_node, created = FileNode.objects.get_or_create(
                    org=org,
                    folder=drawing_folder,
                    slug=slug,
                    defaults=defaults,
                )
                updates = []
                for field, value in defaults.items():
                    if getattr(file_node, field) != value and not (field == "created_by" and not value):
                        setattr(file_node, field, value)
                        updates.append(field)
                if updates:
                    file_node.save(update_fields=updates)
                drawings.append(number)
        drawings.sort()
        return drawings

    def _ensure_placeholder_version(self, file_node, user):
        version = FileVersion.objects.filter(file_node=file_node, version=1).first()
        if version and version.blob:
            if user and version.uploaded_by_id is None:
                version.uploaded_by = user
                version.save(update_fields=["uploaded_by"])
            if file_node.latest_version_id != version.id:
                file_node.latest_version = version
                file_node.save(update_fields=["latest_version"])
            return version

        if not version:
            version = FileVersion(file_node=file_node, version=1)

        if user:
            version.uploaded_by = user

        if not version.blob:
            placeholder = ContentFile(b"%PDF-1.4\n% Farraen placeholder\n", name="blank.pdf")
            version.blob.save("blank.pdf", placeholder, save=False)

        version.save()

        if file_node.latest_version_id != version.id:
            file_node.latest_version = version
            file_node.save(update_fields=["latest_version"])

        return version

    # ------------------------------------------------------------------
    # Weld generation
    # ------------------------------------------------------------------
    def _seed_welds(self, project, heats, welders, rigs, drawings, rng, user, weld_target, day_span):
        if not heats or not welders or not rigs:
            raise RuntimeError("Heats, welders, and rigs must exist before seeding welds")

        today = timezone.localdate()
        start_date = today - timedelta(days=day_span - 1)

        base_per_day = weld_target // day_span
        remainder = weld_target % day_span
        weld_counts = [base_per_day + (1 if idx < remainder else 0) for idx in range(day_span)]

        drawing_sequences = defaultdict(int)
        nde_sequences = defaultdict(int)

        welder_stencils = [w.stencil for w in welders]
        heat_list = list(heats)

        weld_type_choices = [
            (Weld.WeldType.BUTT, 0.65),
            (Weld.WeldType.FILLET, 0.12),
            (Weld.WeldType.BRANCH, 0.1),
            (Weld.WeldType.SOCKET, 0.08),
            (Weld.WeldType.OTHER, 0.05),
        ]
        weld_type_weights = [weight for _, weight in weld_type_choices]
        weld_type_labels = [choice for choice, _ in weld_type_choices]

        repair_types = [
            Weld.RepairType.POROSITY,
            Weld.RepairType.SLAG,
            Weld.RepairType.LACK_OF_FUSION,
            Weld.RepairType.INCOMPLETE_PENETRATION,
            Weld.RepairType.CRACK,
            Weld.RepairType.ARC_STRIKES_OTHER,
            Weld.RepairType.UNDERCUT,
        ]

        disposition_weights = [
            (Weld.Disposition.ACCEPTED, 0.9),
            (Weld.Disposition.PENDING, 0.05),
            (Weld.Disposition.REPAIR, 0.04),
            (Weld.Disposition.CUT_OUT, 0.01),
        ]
        disposition_labels = [label for label, _ in disposition_weights]
        disposition_probs = [weight for _, weight in disposition_weights]

        nde_extra_methods = [
            Weld.NDEType.RADIOGRAPHIC,
            Weld.NDEType.ULTRASONIC,
            Weld.NDEType.MAGNETIC_PARTICLE,
            Weld.NDEType.PENETRANT,
            Weld.NDEType.PHASED_ARRAY,
        ]

        day_dates = [start_date + timedelta(days=offset) for offset in range(day_span)]

        weld_index = 0
        for day_idx, day in enumerate(day_dates):
            welds_today = weld_counts[day_idx]
            for _ in range(welds_today):
                drawing_number = rng.choice(drawings)
                drawing_sequences[drawing_number] += 1
                weld_seq = drawing_sequences[drawing_number]
                weld_id = f"{drawing_number}-W{weld_seq:03d}"

                use_same_heat = rng.random() < 0.2
                primary_heat = rng.choice(heat_list)
                secondary_heat = primary_heat if use_same_heat else rng.choice(heat_list)
                if not use_same_heat and secondary_heat.heat_number == primary_heat.heat_number:
                    # ensure two distinct heats when requested
                    alternatives = [h for h in heat_list if h.heat_number != primary_heat.heat_number]
                    if alternatives:
                        secondary_heat = rng.choice(alternatives)

                material1_defaults = {
                    "material1_heat": primary_heat,
                    "material1_description": f"{primary_heat.description} (WPS {primary_heat.wps_number})",
                    "material1_grade": primary_heat.material_grade,
                    "material1_outer_diameter_in": primary_heat.outer_diameter_in,
                    "material1_wall_thickness_in": primary_heat.wall_thickness_in,
                }
                material2_defaults = {
                    "material2_heat": secondary_heat,
                    "material2_description": f"{secondary_heat.description} (WPS {secondary_heat.wps_number})",
                    "material2_grade": secondary_heat.material_grade,
                    "material2_outer_diameter_in": secondary_heat.outer_diameter_in,
                    "material2_wall_thickness_in": secondary_heat.wall_thickness_in,
                }

                nde_sequences[day] += 1
                nde_seq = nde_sequences[day]
                nde_number = f"NDE-{day:%Y-%m-%d}-{nde_seq:04d}"
                nde_date = day + timedelta(days=rng.choice([0, 0, 0, 1]))

                selected_weld_type = rng.choices(weld_type_labels, weights=weld_type_weights, k=1)[0]

                stencils = rng.sample(welder_stencils, 3)
                root_stencil, fill_stencil, cap_stencil = stencils

                disposition = rng.choices(disposition_labels, weights=disposition_probs, k=1)[0]
                repair_type = None
                if disposition in (Weld.Disposition.REPAIR, Weld.Disposition.CUT_OUT):
                    repair_type = rng.choice(repair_types)
                elif rng.random() < 0.07:
                    repair_type = rng.choice(repair_types)

                disposition_comment = ""
                if disposition != Weld.Disposition.ACCEPTED:
                    reason = {
                        Weld.Disposition.PENDING: "Pending review of radiographs",
                        Weld.Disposition.REPAIR: "Repair required due to detected flaw",
                        Weld.Disposition.CUT_OUT: "Section removed for replacement",
                    }
                    disposition_comment = reason.get(disposition, "")

                additional_methods = []
                for method in nde_extra_methods:
                    if rng.random() < 0.18:
                        additional_methods.append(method)
                additional_methods = sorted(set(additional_methods))
                nde_summary = "VT"
                if additional_methods:
                    method_codes = "+".join(method for method in additional_methods)
                    nde_summary = f"VT+{method_codes}"
                    if disposition_comment:
                        disposition_comment = f"{disposition_comment} (NDE: {nde_summary})"
                elif disposition_comment:
                    disposition_comment = f"{disposition_comment} (NDE: VT)"

                defaults = {
                    "drawing_number": drawing_number,
                    "nde_number": nde_number,
                    "nde_type": Weld.NDEType.VISUAL,
                    "nde_date": nde_date,
                    "nde_rig": rng.choice(rigs),
                    "weld_type": selected_weld_type,
                    "date_welded": day,
                    "welder_stencil_root_hotpass": root_stencil,
                    "welder_stencil_fill": fill_stencil,
                    "welder_stencil_cap": cap_stencil,
                    "welder_stencil_repair": repair_type and rng.choice(welder_stencils) or "",
                    "repair_type": repair_type,
                    "disposition": disposition,
                    "disposition_comment": disposition_comment,
                    "created_by": user,
                    "updated_by": user,
                }
                defaults.update(material1_defaults)
                defaults.update(material2_defaults)

                weld, _ = Weld.objects.update_or_create(
                    project=project,
                    weld_id=weld_id,
                    defaults=defaults,
                )
                weld_index += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded or updated {weld_index} welds."))
