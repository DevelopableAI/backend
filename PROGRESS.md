# Progress day by day

## 02/19/2026
- I built a quick MVP that runs on the terminal.
- It is able to generate code at 0.01$ cost.
- Highly dependent on Jinja templates (which is good and not so good)
- Completely working API with no compilation errors and works successfully so far
- I have tested Create User, Get User By ID, List User, Health check endpoints.
- I have to test other endpoints

## 02/20/2026
- MVP still runs on terminal
- Added logging with windsor as a capability
- Improved error messages on bad request scenario
- Added claude code as a contributor to the project
- Solidified localhost postgres server issues for generated API service to launch and maintain its database on this server

# 02/20/2026 [Problems detected]
- MVP output does not handle data storage security (password got stored in plain text. Need the generator to understand that we might be storing sensitive data and need an encryption key of some sort to do that. This should be true for any sort of sensitive data.)
- MVP output does not have endpoints that relate between different entities. (If user and post tables are related by authorId, then there must be endpoints indicating that.)
- MVP output does not handle basic authorization (a post can only be edited by the author themselves and not anyone else.)