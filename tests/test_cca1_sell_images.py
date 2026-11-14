"""CCA-1: /sell sends the Gumroad pictures (thumbnail + cover + gallery) when gumroad_images is there.

gumroad_images is built by another worker, so here it is a small stand-in module placed in
sys.modules (every accepted shape is tried). "Not installed" is forced the same way, so these tests
give the same answer before and after the real module lands. Packs live in temp folders; the send
functions are recorders; the reachability tests replace telegram_inbox's send functions.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_cc1_sell as sell_fixture   # the /sell fixtures (temp packs, recorder); not re-collected here
import tg_sell

PNG = sell_fixture.PNG
SAMPLE_CHAT_ID = sell_fixture.SAMPLE_CHAT_ID


def fake_module(**functions):
    module = types.ModuleType("gumroad_images")
    for name, function in functions.items():
        setattr(module, name, function)
    return module


class Base(sell_fixture.Base):
    def install(self, module):
        """Make `import gumroad_images` give this module (None = not installed)."""
        patcher = mock.patch.dict(sys.modules, {"gumroad_images": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def gumroad_files(self, pack, folder="gumroad", names=("thumbnail.png", "cover.png", "gallery-1.png", "gallery-2.png")):
        target = pack / folder
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            (target / name).write_bytes(PNG)
        return target

    def standard(self, pack):
        """A pack with a gumroad folder and a module that returns the dict shape."""
        folder = self.gumroad_files(pack)
        self.install(fake_module(upload_order=lambda pack_dir: {
            "thumbnail": folder / "thumbnail.png", "cover": folder / "cover.png",
            "gallery": [folder / "gallery-1.png", folder / "gallery-2.png"]}))
        return folder

    def names(self, pairs):
        return [(role, path.name) for role, path in pairs]


class NotInstalledTests(Base):
    """Today's behaviour stays when gumroad_images is not there."""

    def setUp(self):
        super().setUp()
        self.install(None)

    def test_module_is_reported_missing(self):
        self.assertIsNone(tg_sell._gumroad_module())

    def test_no_gumroad_set(self):
        self.assertEqual(tg_sell.gumroad_image_set(self.make_pack()), [])

    def test_sell_images_are_cover_and_previews(self):
        pack = self.make_pack(previews=2)
        self.assertEqual(self.names(tg_sell.sell_images(pack)),
                         [("cover", "cover.png"), ("gallery", "preview-1.png"), ("gallery", "preview-2.png")])

    def test_sell_sends_cover_then_previews_with_the_old_captions(self):
        self.make_pack(previews=2)
        rec, reply = self.sell()
        self.assertEqual(reply, {"handled": True})
        self.assertEqual([Path(path).name for path, _ in rec.photos], ["cover.png", "preview-1.png", "preview-2.png"])
        self.assertEqual([caption for _, caption in rec.photos],
                         ["3/4 Pack #1 Cover image: cover.png", "3/4 Pack #1 Preview 1: preview-1.png",
                          "3/4 Pack #1 Preview 2: preview-2.png"])

    def test_checklist_keeps_the_old_thumbnail_step(self):
        self.make_pack(previews=1)
        rec, _ = self.sell()
        self.assertIn("'Thumbnail' ki ade cover image upload cheyandi.", rec.all_text)
        self.assertIn("cover.png upload cheyandi. Tarvata previews kuda add cheyandi: preview-1.png.", rec.all_text)

    def test_gumroad_folder_alone_is_ignored_without_the_module(self):
        pack = self.make_pack()
        self.gumroad_files(pack)
        rec, _ = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.photos], ["cover.png", "preview-1.png", "preview-2.png"])
        self.assertTrue(all(Path(path).parent == pack for path, _ in rec.photos))

    def test_broken_module_import_is_treated_as_missing(self):
        with mock.patch.dict(sys.modules, {"gumroad_images": None}):
            self.assertEqual(tg_sell.gumroad_image_set(self.make_pack(name="2-x")), [])


class GumroadSetTests(Base):
    """Every shape gumroad_images may answer with."""

    def test_dict_shape(self):
        pack = self.make_pack()
        self.standard(pack)
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumbnail.png"), ("cover", "cover.png"),
                          ("gallery", "gallery-1.png"), ("gallery", "gallery-2.png")])

    def test_upload_order_is_thumbnail_cover_gallery_even_when_given_in_another_order(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack)
        self.install(fake_module(upload_order=lambda pack_dir: {
            "gallery": [folder / "gallery-2.png", folder / "gallery-1.png"],
            "cover": folder / "cover.png", "thumbnail": folder / "thumbnail.png"}))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumbnail.png"), ("cover", "cover.png"),
                          ("gallery", "gallery-2.png"), ("gallery", "gallery-1.png")])

    def test_list_of_paths_gets_roles_from_file_names(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack)
        self.install(fake_module(upload_order=lambda pack_dir: [
            str(folder / "gallery-1.png"), str(folder / "cover.png"), str(folder / "thumbnail.png")]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumbnail.png"), ("cover", "cover.png"), ("gallery", "gallery-1.png")])

    def test_list_of_role_path_pairs(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("a.png", "b.png", "c.png"))
        self.install(fake_module(upload_order=lambda pack_dir: [
            ("gallery", folder / "c.png"), ("thumbnail", folder / "a.png"), (folder / "b.png", "cover")]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "a.png"), ("cover", "b.png"), ("gallery", "c.png")])

    def test_list_of_dicts(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("a.png", "b.png", "c.png"))
        self.install(fake_module(upload_order=lambda pack_dir: [
            {"role": "cover", "path": str(folder / "b.png")}, {"role": "thumbnail", "file": str(folder / "a.png")},
            {"kind": "gallery", "path": str(folder / "c.png")}]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "a.png"), ("cover", "b.png"), ("gallery", "c.png")])

    def test_other_key_names_are_understood(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack)
        self.install(fake_module(upload_order=lambda pack_dir: {
            "thumb": folder / "thumbnail.png", "hero": folder / "cover.png", "previews": [folder / "gallery-1.png"],
            "notes": "ignored"}))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumbnail.png"), ("cover", "cover.png"), ("gallery", "gallery-1.png")])

    def test_relative_paths_are_resolved_inside_the_pack(self):
        pack = self.make_pack()
        self.gumroad_files(pack)
        self.install(fake_module(upload_order=lambda pack_dir: {
            "thumbnail": "gumroad/thumbnail.png", "cover": "gumroad/cover.png", "gallery": ["gumroad/gallery-1.png"]}))
        chosen = tg_sell.gumroad_image_set(pack)
        self.assertEqual([path.name for _, path in chosen], ["thumbnail.png", "cover.png", "gallery-1.png"])
        self.assertTrue(all(path.is_absolute() for _, path in chosen))

    def test_every_accepted_function_name(self):
        for number, name in enumerate(tg_sell.GUMROAD_IMAGE_FUNCTIONS, 40):
            with self.subTest(name=name):
                pack = self.make_pack(name=f"{number}-function-pack")
                folder = self.gumroad_files(pack, names=("thumbnail.png",))
                with mock.patch.dict(sys.modules, {"gumroad_images": fake_module(**{name: lambda pack_dir, f=folder: [f / "thumbnail.png"]})}):
                    self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("thumbnail", "thumbnail.png")])

    def test_function_gets_the_pack_folder(self):
        pack = self.make_pack()
        seen = []
        self.install(fake_module(upload_order=lambda pack_dir: seen.append(pack_dir) or []))
        tg_sell.gumroad_image_set(pack)
        self.assertEqual(seen, [pack])

    def test_module_without_function_uses_the_gumroad_folder(self):
        for number, folder_name in enumerate(tg_sell.GUMROAD_IMAGE_FOLDERS, 20):
            with self.subTest(folder=folder_name):
                pack = self.make_pack(name=f"{number}-folder-pack")
                self.gumroad_files(pack, folder=folder_name,
                                   names=("gallery-10.png", "gallery-2.png", "cover.png", "thumbnail.png"))
                with mock.patch.dict(sys.modules, {"gumroad_images": fake_module()}):
                    self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                                     [("thumbnail", "thumbnail.png"), ("cover", "cover.png"),
                                      ("gallery", "gallery-2.png"), ("gallery", "gallery-10.png")])

    def test_folder_in_the_marketing_folder_is_found(self):
        pack = self.make_pack()
        target = self.marketing / pack.name / "gumroad"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("thumbnail.png", "cover.png"):
            (target / name).write_bytes(PNG)
        self.install(fake_module())
        chosen = tg_sell.gumroad_image_set(pack, self.recorder().ctx)
        self.assertEqual(self.names(chosen), [("thumbnail", "thumbnail.png"), ("cover", "cover.png")])
        self.assertTrue(all(path.parent == target.resolve() for _, path in chosen))

    def test_function_error_falls_back_to_the_folder(self):
        pack = self.make_pack()
        self.gumroad_files(pack, names=("thumbnail.png",))

        def broken(pack_dir):
            raise RuntimeError("image tool broke")
        self.install(fake_module(upload_order=broken))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("thumbnail", "thumbnail.png")])

    def test_empty_answer_means_no_set(self):
        pack = self.make_pack()
        for answer in (None, [], {}, "", {"thumbnail": None, "cover": "", "gallery": []}):
            with self.subTest(answer=answer):
                with mock.patch.dict(sys.modules, {"gumroad_images": fake_module(upload_order=lambda pack_dir, a=answer: a)}):
                    self.assertEqual(tg_sell.gumroad_image_set(pack), [])

    def test_missing_files_are_left_out(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("cover.png",))
        self.install(fake_module(upload_order=lambda pack_dir: {
            "thumbnail": folder / "not-there.png", "cover": folder / "cover.png", "gallery": [folder / "gone.png"]}))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("cover", "cover.png")])

    def test_files_outside_the_pack_are_never_sent(self):
        pack = self.make_pack()
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        other_pack = self.make_pack(name="2-other-pack")
        self.install(fake_module(upload_order=lambda pack_dir: {
            "thumbnail": outside, "cover": other_pack / "cover.png", "gallery": ["../2-other-pack/preview-1.png"]}))
        self.assertEqual(tg_sell.gumroad_image_set(pack), [])

    def test_symlinks_are_never_sent(self):
        pack = self.make_pack()
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        folder = self.gumroad_files(pack, names=("cover.png",))
        try:
            (folder / "thumbnail.png").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks not available")
        self.install(fake_module(upload_order=lambda pack_dir: [folder / "thumbnail.png", folder / "cover.png"]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("cover", "cover.png")])

    def test_non_image_files_are_left_out(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("cover.png",))
        (folder / "notes.txt").write_text("notes", encoding="utf-8")
        (folder / "thumbnail.pdf").write_bytes(b"%PDF")
        self.install(fake_module())
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("cover", "cover.png")])

    def test_duplicates_are_sent_once(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("cover.png",))
        self.install(fake_module(upload_order=lambda pack_dir: {"cover": folder / "cover.png",
                                                                "gallery": [folder / "cover.png"]}))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("cover", "cover.png")])

    def test_second_thumbnail_and_cover_become_gallery(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("thumb-a.png", "thumb-b.png", "cover-a.png", "cover-b.png"))
        self.install(fake_module(upload_order=lambda pack_dir: [folder / "thumb-a.png", folder / "thumb-b.png",
                                                                folder / "cover-a.png", folder / "cover-b.png"]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumb-a.png"), ("cover", "cover-a.png"),
                          ("gallery", "thumb-b.png"), ("gallery", "cover-b.png")])

    def test_at_most_max_images(self):
        pack = self.make_pack()
        names = ["thumbnail.png", "cover.png"] + [f"gallery-{index}.png" for index in range(1, 15)]
        self.gumroad_files(pack, names=names)
        self.install(fake_module())
        chosen = tg_sell.gumroad_image_set(pack)
        self.assertEqual(len(chosen), tg_sell.MAX_IMAGES)
        self.assertEqual(self.names(chosen)[:3], [("thumbnail", "thumbnail.png"), ("cover", "cover.png"),
                                                  ("gallery", "gallery-1.png")])

    def test_jpg_and_webp_are_images_too(self):
        pack = self.make_pack()
        self.gumroad_files(pack, names=("thumbnail.jpg", "cover.webp", "gallery-1.jpeg"))
        self.install(fake_module())
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)),
                         [("thumbnail", "thumbnail.jpg"), ("cover", "cover.webp"), ("gallery", "gallery-1.jpeg")])

    def test_unknown_things_in_the_answer_are_skipped(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("cover.png",))
        self.install(fake_module(upload_order=lambda pack_dir: [None, 5, {"role": "cover"}, ("x", "y", "z"),
                                                                folder / "cover.png"]))
        self.assertEqual(self.names(tg_sell.gumroad_image_set(pack)), [("cover", "cover.png")])


class SellWithGumroadImagesTests(Base):
    """/sell N end to end with the Gumroad set."""

    def test_pictures_go_out_in_upload_order(self):
        self.standard(self.make_pack())
        rec, reply = self.sell()
        self.assertEqual(reply, {"handled": True})
        self.assertEqual([Path(path).name for path, _ in rec.photos],
                         ["thumbnail.png", "cover.png", "gallery-1.png", "gallery-2.png"])

    def test_captions_name_the_role(self):
        self.standard(self.make_pack())
        rec, _ = self.sell()
        self.assertEqual([caption for _, caption in rec.photos],
                         ["3/4 Pack #1 Thumbnail (square): thumbnail.png", "3/4 Pack #1 Cover image: cover.png",
                          "3/4 Pack #1 Gallery 1: gallery-1.png", "3/4 Pack #1 Gallery 2: gallery-2.png"])

    def test_the_gumroad_files_are_sent_not_the_pack_previews(self):
        pack = self.make_pack(previews=3)
        folder = self.standard(pack)
        rec, _ = self.sell()
        self.assertTrue(all(Path(path).parent == folder.resolve() for path, _ in rec.photos))
        self.assertNotIn("preview-1.png", [Path(path).name for path, _ in rec.photos])

    def test_checklist_names_thumbnail_cover_and_gallery(self):
        self.standard(self.make_pack())
        rec, _ = self.sell()
        self.assertIn("'Thumbnail' section lo thumbnail.png upload cheyandi (square image).", rec.all_text)
        self.assertIn("cover.png upload cheyandi. Tarvata previews kuda add cheyandi: gallery-1.png, gallery-2.png.",
                      rec.all_text)
        self.assertNotIn("'Thumbnail' ki ade cover image", rec.all_text)

    def test_order_of_messages_is_unchanged(self):
        self.standard(self.make_pack())
        rec, _ = self.sell()
        self.assertEqual(rec.order, ["text", "text", "document", "document", "photo", "photo", "photo", "photo",
                                     "text", "text"])
        self.assertIn("4 images pampanu", rec.texts[-1])

    def test_product_files_are_unchanged(self):
        self.standard(self.make_pack())
        rec, _ = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.documents], ["product.zip", "LICENSE.txt"])

    def test_gumroad_images_are_never_sent_as_buyer_files(self):
        self.standard(self.make_pack())
        rec, _ = self.sell()
        self.assertFalse([path for path, _ in rec.documents if path.endswith(".png")])

    def test_photo_failure_falls_back_to_document(self):
        self.standard(self.make_pack())
        rec, _ = self.sell(photo_ok=False)
        image_documents = [Path(path).name for path, caption in rec.documents if caption.startswith("3/4")]
        self.assertEqual(image_documents, ["thumbnail.png", "cover.png", "gallery-1.png", "gallery-2.png"])

    def test_set_without_thumbnail_keeps_the_same_cover_step(self):
        pack = self.make_pack()
        folder = self.gumroad_files(pack, names=("cover.png", "gallery-1.png"))
        self.install(fake_module(upload_order=lambda pack_dir: {"cover": folder / "cover.png",
                                                                "gallery": [folder / "gallery-1.png"]}))
        rec, _ = self.sell()
        self.assertEqual([caption for _, caption in rec.photos],
                         ["3/4 Pack #1 Cover image: cover.png", "3/4 Pack #1 Preview 1: gallery-1.png"])
        self.assertIn("'Thumbnail' ki ade cover image upload cheyandi.", rec.all_text)

    def test_empty_set_falls_back_to_cover_and_previews(self):
        self.make_pack(previews=1)
        self.install(fake_module(upload_order=lambda pack_dir: []))
        rec, _ = self.sell()
        self.assertEqual([Path(path).name for path, _ in rec.photos], ["cover.png", "preview-1.png"])

    def test_not_yet_pack_sends_no_pictures_at_all(self):
        self.truth = {"verdict": "needs fix", "score": 30, "problems": ["x"]}
        self.standard(self.make_pack())
        rec, reply = self.sell()
        self.assertIn("NOT YET", reply["text"])
        self.assertEqual(rec.photos, [])

    def test_text_only_caller_gets_the_checklist_with_the_thumbnail(self):
        pack = self.make_pack()
        self.standard(pack)
        reply = tg_sell.handle("/sell 1", {"products_dir": str(self.products), "marketing_dir": str(self.marketing)})
        self.assertIn("'Thumbnail' section lo thumbnail.png", reply["text"])

    def test_no_money_in_anything_sent(self):
        import store_kits
        self.standard(self.make_pack())
        rec, _ = self.sell()
        for text in rec.texts + [caption for _, caption in rec.photos]:
            self.assertIsNone(store_kits.check_money(text))


class HelperTests(unittest.TestCase):
    def test_labels_without_roles_are_the_old_ones(self):
        self.assertEqual(tg_sell.image_labels([Path("a.png"), Path("b.png"), Path("c.png")]),
                         ["Cover image", "Preview 1", "Preview 2"])

    def test_labels_with_a_thumbnail(self):
        self.assertEqual(tg_sell.image_labels([Path("t.png"), Path("c.png"), Path("g.png")],
                                              ["thumbnail", "cover", "gallery"]),
                         ["Thumbnail (square)", "Cover image", "Gallery 1"])

    def test_labels_ignore_a_wrong_length_role_list(self):
        self.assertEqual(tg_sell.image_labels([Path("a.png")], ["cover", "gallery"]), ["Cover image"])

    def test_role_from_name(self):
        self.assertEqual(tg_sell._role_from_name("Thumbnail_600.png"), "thumbnail")
        self.assertEqual(tg_sell._role_from_name("square.png"), "thumbnail")
        self.assertEqual(tg_sell._role_from_name("gumroad-cover.png"), "cover")
        self.assertEqual(tg_sell._role_from_name("page-3.png"), "gallery")

    def test_checklist_text_old_call_still_works(self):
        text = tg_sell.checklist_text(4, [Path("product.zip")], [Path("cover.png"), Path("preview-1.png")])
        self.assertIn("cover.png upload cheyandi. Tarvata previews kuda add cheyandi: preview-1.png.", text)
        self.assertIn("'Thumbnail' ki ade cover image upload cheyandi.", text)

    def test_checklist_text_without_images(self):
        text = tg_sell.checklist_text(4, [Path("product.zip")], [])
        self.assertIn("cover image upload cheyandi.", text)


class ReachabilityTests(Base):
    """Through telegram_inbox.call_plugins with fake send functions."""

    def setUp(self):
        super().setUp()
        import telegram_inbox
        self.inbox = telegram_inbox
        self.texts, self.documents, self.photos = [], [], []
        self.db = str(self.root / "robot.db")
        patches = (
            mock.patch.dict(os.environ, {"CHILLI_PRODUCTS_DIR": str(self.products),
                                         "CHILLI_MARKETING_DIR": str(self.marketing)}),
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "send_document",
                              side_effect=lambda token, chat, path, caption="": self.documents.append(path) or True),
            mock.patch.object(telegram_inbox, "send_photo",
                              side_effect=lambda token, chat, path, caption="": self.photos.append((path, caption)) or True),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text):
        return self.inbox.call_plugins(text, "test-token", SAMPLE_CHAT_ID, db_path=self.db)

    def test_gumroad_pictures_through_call_plugins(self):
        self.standard(self.make_pack())
        handled, reply = self.call("/sell 1")
        self.assertTrue(handled)
        self.assertEqual(reply, {"handled": True})
        self.assertEqual([Path(path).name for path, _ in self.photos],
                         ["thumbnail.png", "cover.png", "gallery-1.png", "gallery-2.png"])
        self.assertEqual(self.photos[0][1], "3/4 Pack #1 Thumbnail (square): thumbnail.png")

    def test_without_the_module_through_call_plugins(self):
        self.install(None)
        self.make_pack()
        handled, _ = self.call("pack 1 sell chey")
        self.assertTrue(handled)
        self.assertEqual([Path(path).name for path, _ in self.photos], ["cover.png", "preview-1.png", "preview-2.png"])


if __name__ == "__main__":
    unittest.main()
