# Voice Licensing

ReadAloudTTS does not include voice models. Voice files are downloaded separately by the user.

Every Piper voice can have its own model card, dataset source, and license terms. Review those terms before commercial, public, client, or redistributed use.

## Included downloader options

### en_US-lessac-medium

- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD
- Note: review the linked Lessac dataset license before commercial or public use.

### en_US-amy-medium

- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/amy/medium/MODEL_CARD
- Note: the model card points to the Mycroft mimic3 voice source and says to see that URL for license details.

### en_US-hfc_female-medium

- Model card: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/hfc_female/medium/MODEL_CARD
- Sensitive note: the model card references CC BY-NC-SA 4.0 dataset licensing. Treat this voice as non-commercial/share-alike sensitive unless you have reviewed and confirmed your use is allowed.

## Repository policy

- Do not commit `.onnx` files.
- Do not commit downloaded `.onnx.json` files.
- Do not commit generated WAV files.
- Do not redistribute downloaded voices from this repository.
- Keep voice licensing notes visible in installer and downloader flows.
