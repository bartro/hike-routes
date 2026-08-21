# Contributing to Hike Routes

Thanks for your interest in contributing!

## How to Contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Development

```bash
# Generate HTML from GPX files
python3 generate.py

# Run the dev server
python3 -m http.server 8081 --directory output
```

## Code Style

- Python: PEP 8, 4-space indentation
- HTML/CSS: 2-space indentation
- All files: UTF-8, LF line endings, no trailing whitespace

## Reporting Issues

When reporting issues, please include:
- GPX file details (point count, elevation range)
- Browser and version
- Any error messages
